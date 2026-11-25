//! Exercise Tauri's downloader and verifier without connecting to a release channel.

use serde_json::{json, Value};
use std::io::{Read, Write};
use std::net::TcpListener;
use std::thread::{self, JoinHandle};
use std::time::Duration;
use tauri::test::{mock_builder, mock_context, noop_assets};
use tauri::{App, Url};
use tauri_plugin_updater::{Error, Updater, UpdaterExt};

// Only the public key and signatures are fixtures. The Tauri CLI generated a disposable key.
const KEY: &str = include_str!("fixtures/update.pub");
const SIGNATURE: &str = include_str!("fixtures/update.sig");
const NO_VERSION: &str = include_str!("fixtures/unversioned.sig");
const ASSET: &[u8] = b"cortexshift-distribution-fixture";

fn updater(endpoint: Url, timeout: Duration) -> (App<tauri::test::MockRuntime>, Updater) {
    let mut context = mock_context(noop_assets());
    context.package_info_mut().version = "0.2.0".parse().unwrap();
    context.config_mut().plugins.0.insert(
        "updater".into(),
        json!({
            "pubkey": KEY.trim(),
            "requireSignedVersion": true,
            "dangerousInsecureTransportProtocol": true,
        }),
    );
    let app = mock_builder()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .build(context)
        .unwrap();
    let updater = app
        .updater_builder()
        .target("fixture-target")
        .executable_path("fixture.app/Contents/MacOS/app")
        .endpoints(vec![endpoint])
        .unwrap()
        .no_proxy()
        .timeout(timeout)
        .build()
        .unwrap();
    (app, updater)
}

fn response(bytes: Vec<u8>, delay: Duration) -> (Url, JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/test", listener.local_addr().unwrap())
        .parse()
        .unwrap();
    let task = thread::spawn(move || {
        let (mut client, _) = listener.accept().unwrap();
        client
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        let mut headers = Vec::new();
        let mut chunk = [0; 512];
        while !headers.ends_with(b"\r\n\r\n") {
            let received = client.read(&mut chunk).unwrap();
            assert_ne!(received, 0);
            headers.extend(&chunk[..received]);
        }
        thread::sleep(delay);
        client.write_all(&bytes).ok();
    });
    (url, task)
}

fn body(content: &[u8]) -> Vec<u8> {
    let mut response = format!(
        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        content.len()
    )
    .into_bytes();
    response.extend_from_slice(content);
    response
}

fn manifest(url: &Url, signature: &str) -> Value {
    json!({
        "version": "0.3.0",
        "notes": "Fixture notes",
        "platforms": {"fixture-target": {"url": url.as_str(), "signature": signature.trim()}},
    })
}

#[tokio::test]
async fn checks_valid_metadata_and_downloads_verified_bytes() {
    let (asset, download) = response(body(ASSET), Duration::ZERO);
    let manifest = serde_json::to_vec(&manifest(&asset, SIGNATURE)).unwrap();
    let (feed, server) = response(body(&manifest), Duration::ZERO);
    let (_app, updater) = updater(feed, Duration::from_secs(4));
    let update = updater.check().await.unwrap().unwrap();
    assert_eq!(update.body.as_deref(), Some("Fixture notes"));
    let mut received = 0;
    let bytes = update
        .download(|size, _| received += size, || {})
        .await
        .unwrap();
    assert_eq!(bytes, ASSET);
    assert_eq!(received, ASSET.len());
    assert!(update.verify(b"tampered").is_err());
    assert!(update.install(bytes).is_err()); // A signed text fixture is not an installer.
    server.join().unwrap();
    download.join().unwrap();
}

#[test]
fn rejects_missing_signature_version_mismatch_and_unsupported_target() {
    let url = "http://127.0.0.1:1/test".parse().unwrap();
    let (_app, updater) = updater(url, Duration::from_secs(1));
    let asset = "https://example.invalid/asset".parse().unwrap();
    let unsigned_version = updater
        .restore_update(manifest(&asset, NO_VERSION))
        .unwrap()
        .unwrap();
    assert!(matches!(
        unsigned_version.verify(ASSET),
        Err(Error::MissingSignedVersion)
    ));
    let mut newer = manifest(&asset, SIGNATURE);
    newer["version"] = json!("0.4.0");
    let replay = updater.restore_update(newer).unwrap().unwrap();
    assert!(matches!(
        replay.verify(ASSET),
        Err(Error::SignedVersionMismatch { .. })
    ));
    let mut malformed = manifest(&asset, SIGNATURE);
    malformed["platforms"]["fixture-target"]
        .as_object_mut()
        .unwrap()
        .remove("signature");
    assert!(updater.restore_update(malformed).is_err());
    let mut unsupported = manifest(&asset, SIGNATURE);
    unsupported["platforms"] = json!({});
    assert!(matches!(
        updater.restore_update(unsupported),
        Err(Error::TargetNotFound(_)) | Err(Error::TargetsNotFound(_))
    ));
    for old in ["0.2.0", "0.1.0"] {
        let mut manifest = manifest(&asset, SIGNATURE);
        manifest["version"] = json!(old);
        assert!(updater.restore_update(manifest).unwrap().is_none());
    }
}

#[tokio::test]
async fn handles_bad_manifest_no_update_and_network_timeout() {
    for (bytes, delay, expected) in [
        (body(b"not JSON"), Duration::ZERO, "invalid"),
        (
            b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n".to_vec(),
            Duration::ZERO,
            "empty",
        ),
        (body(b"{}"), Duration::from_millis(800), "timeout"),
    ] {
        let (endpoint, server) = response(bytes, delay);
        let (_app, updater) = updater(endpoint, Duration::from_millis(500));
        let result = updater.check().await;
        match expected {
            "invalid" => assert!(matches!(result, Err(Error::Reqwest(error)) if error.is_decode())),
            "empty" => assert!(result.unwrap().is_none()),
            _ => assert!(matches!(result, Err(Error::Reqwest(error)) if error.is_timeout())),
        }
        server.join().unwrap();
    }
}

#[tokio::test]
async fn refuses_missing_and_incomplete_downloads() {
    for bytes in [
        b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".to_vec(),
        b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\nincomplete".to_vec(),
    ] {
        let (endpoint, server) = response(bytes, Duration::ZERO);
        let metadata = serde_json::to_vec(&manifest(&endpoint, SIGNATURE)).unwrap();
        let (feed, metadata_server) = response(body(&metadata), Duration::ZERO);
        let (_app, updater) = updater(feed, Duration::from_secs(4));
        let update = updater.check().await.unwrap().unwrap();
        assert!(update.download(|_, _| {}, || {}).await.is_err());
        metadata_server.join().unwrap();
        server.join().unwrap();
    }
}

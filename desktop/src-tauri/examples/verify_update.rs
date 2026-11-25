//! Verify release assets before staging them for publication.

use base64::prelude::*;
use minisign_verify::{PublicKey, Signature};
use std::{env, error::Error, fs};

fn main() -> Result<(), Box<dyn Error>> {
    let args = env::args_os().skip(1).collect::<Vec<_>>();
    let [config, asset, sig, version] = args.as_slice() else {
        return Err(
            "usage: verify_update <signing-config> <artifact> <signature> <version>".into(),
        );
    };
    let config: serde_json::Value = serde_json::from_slice(&fs::read(config)?)?;
    let key = config["plugins"]["updater"]["pubkey"]
        .as_str()
        .ok_or("missing trusted public key")?;
    let public = PublicKey::decode(&String::from_utf8(BASE64_STANDARD.decode(key)?)?)?;
    let sig = fs::read_to_string(sig)?;
    let signature = Signature::decode(&String::from_utf8(BASE64_STANDARD.decode(sig.trim())?)?)?;
    public.verify(&fs::read(asset)?, &signature, true)?;
    let signed = signature
        .trusted_comment()
        .split('\t')
        .find_map(|field| field.strip_prefix("version:"));
    if signed.is_none() || signed != version.to_str() {
        return Err("signature is for a different or missing Desktop version".into());
    }
    println!("Verified the release signature and signed Desktop version.");
    Ok(())
}

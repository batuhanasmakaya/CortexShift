use crate::bridge::BridgeError;
use serde::{Deserialize, Serialize};
use std::fs;
use std::io::{self, Read};
use std::path::PathBuf;
use std::sync::Mutex;

const LIBRARY_BYTES: u64 = 1024 * 1024;
const MAX_PROJECTS: usize = 100;

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Project {
    pub project_id: String,
    pub root: String,
}

pub struct Library {
    file: PathBuf,
    lock: Mutex<()>,
}

impl Library {
    pub fn new(file: PathBuf) -> Self {
        Self {
            file,
            lock: Mutex::new(()),
        }
    }

    pub fn list(&self) -> Result<Vec<Project>, BridgeError> {
        let _guard = self.lock.lock().map_err(|_| library_error())?;
        self.load()
    }

    pub fn get(&self, root: &str) -> Result<Project, BridgeError> {
        self.list()?
            .into_iter()
            .find(|project| project.root == root)
            .ok_or_else(|| {
                BridgeError::new(
                    "unregistered_project",
                    "Open this project in the library first.",
                )
            })
    }

    pub fn register(&self, project: Project) -> Result<Project, BridgeError> {
        let _guard = self.lock.lock().map_err(|_| library_error())?;
        let mut projects = self.load()?;
        if let Some(existing) = projects.iter().find(|entry| entry.root == project.root) {
            if existing == &project {
                return Ok(project);
            }
            return Err(BridgeError::new(
                "identity_mismatch",
                "This folder contains a different project. Remove the old library entry before opening it again.",
            ));
        }
        if projects.len() >= MAX_PROJECTS {
            return Err(BridgeError::new(
                "library_full",
                "The library holds up to 100 projects.",
            ));
        }
        projects.push(project.clone());
        self.save(&projects)?;
        Ok(project)
    }

    pub fn remove(&self, root: &str) -> Result<Vec<Project>, BridgeError> {
        let _guard = self.lock.lock().map_err(|_| library_error())?;
        let mut projects = self.load()?;
        let previous = projects.len();
        projects.retain(|entry| entry.root != root);
        if previous != projects.len() {
            self.save(&projects)?;
        }
        Ok(projects)
    }

    fn load(&self) -> Result<Vec<Project>, BridgeError> {
        let file = match fs::File::open(&self.file) {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
            Err(_) => return Err(library_error()),
        };
        let mut bytes = Vec::new();
        file.take(LIBRARY_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| library_error())?;
        if bytes.len() as u64 > LIBRARY_BYTES {
            return Err(library_error());
        }
        let projects: Vec<Project> = serde_json::from_slice(&bytes).map_err(|_| library_error())?;
        if projects.len() > MAX_PROJECTS
            || projects.iter().any(|p| {
                !std::path::Path::new(&p.root).is_absolute()
                    || p.root.len() > 32_768
                    || p.project_id.is_empty()
                    || p.project_id.len() > 256
            })
        {
            return Err(library_error());
        }
        Ok(projects)
    }

    fn save(&self, projects: &[Project]) -> Result<(), BridgeError> {
        let parent = self.file.parent().ok_or_else(library_error)?;
        fs::create_dir_all(parent).map_err(|_| library_error())?;
        let file = tempfile::NamedTempFile::new_in(parent).map_err(|_| library_error())?;
        let contents = serde_json::to_vec(projects).map_err(|_| library_error())?;
        if contents.len() as u64 > LIBRARY_BYTES {
            return Err(BridgeError::new(
                "library_full",
                "The library configuration is full.",
            ));
        }
        use std::io::Write;
        file.as_file()
            .write_all(&contents)
            .map_err(|_| library_error())?;
        file.as_file().sync_all().map_err(|_| library_error())?;
        file.persist(&self.file).map_err(|_| library_error())?;
        Ok(())
    }
}

fn library_error() -> BridgeError {
    BridgeError::new(
        "library_unavailable",
        "The local project library is unreadable or cannot be saved. Check the Desktop configuration directory.",
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn library_persists_only_identity_and_removal_preserves_workspace() {
        let tmp = tempfile::tempdir().unwrap();
        let workspace = tmp.path().join("project");
        fs::create_dir(&workspace).unwrap();
        let file = tmp.path().join("config/projects.json");
        let library = Library::new(file.clone());
        let project = Project {
            project_id: "test".into(),
            root: workspace.to_string_lossy().into(),
        };
        library.register(project.clone()).unwrap();
        library.register(project.clone()).unwrap();
        assert_eq!(
            Library::new(file).list().unwrap(),
            std::slice::from_ref(&project)
        );
        let replacement = Project {
            project_id: "other".into(),
            ..project.clone()
        };
        assert_eq!(
            library.register(replacement).unwrap_err().code,
            "identity_mismatch"
        );
        assert!(library.remove(&project.root).unwrap().is_empty());
        assert!(workspace.is_dir());
    }
}

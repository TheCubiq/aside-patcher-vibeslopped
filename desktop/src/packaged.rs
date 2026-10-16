use sha2::{Digest, Sha256};
use std::{fs, io::Write, path::PathBuf};

const ENGINE: &[u8] = include_bytes!(env!("ASIDE_PATCHER_ENGINE"));

pub fn engine_path() -> Result<PathBuf, String> {
    let digest = format!("{:x}", Sha256::digest(ENGINE));
    let root =
        PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA is unavailable")?)
            .join("AsidePatcher")
            .join("runtime")
            .join(&digest);
    fs::create_dir_all(&root).map_err(|error| error.to_string())?;
    let path = root.join("engine.exe");
    if path.exists() {
        let bytes = fs::read(&path).map_err(|error| error.to_string())?;
        if format!("{:x}", Sha256::digest(&bytes)) != digest {
            return Err("Bundled engine integrity check failed; remove its runtime cache and reopen the app".into());
        }
        return Ok(path);
    }
    let temporary = root.join(format!("engine-{}.tmp", std::process::id()));
    let mut file = fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&temporary)
        .map_err(|error| error.to_string())?;
    file.write_all(ENGINE).map_err(|error| error.to_string())?;
    file.sync_all().map_err(|error| error.to_string())?;
    drop(file);
    if let Err(error) = fs::rename(&temporary, &path) {
        let _ = fs::remove_file(&temporary);
        let existing = fs::read(&path).map_err(|_| error.to_string())?;
        if format!("{:x}", Sha256::digest(&existing)) != digest {
            return Err("Bundled engine integrity check failed".into());
        }
    }
    Ok(path)
}

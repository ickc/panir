//! The package: its license is the repository's, and the tests that read
//! the repository's corpus are left out of it (Cargo.toml's `exclude`).

use std::fs::read_to_string;
use std::path::Path;

#[test]
fn the_license_is_the_repositorys() {
    let here = Path::new(env!("CARGO_MANIFEST_DIR"));
    assert_eq!(
        read_to_string(here.join("LICENSE")).unwrap(),
        read_to_string(here.join("../LICENSE")).unwrap()
    );
}

#[test]
fn tests_that_need_the_corpus_are_not_packaged() {
    let here = Path::new(env!("CARGO_MANIFEST_DIR"));
    let manifest = read_to_string(here.join("Cargo.toml")).unwrap();
    let exclude = manifest
        .lines()
        .find(|l| l.starts_with("exclude = "))
        .expect("an exclude");
    for entry in std::fs::read_dir(here.join("tests")).unwrap() {
        let path = entry.unwrap().path();
        let name = format!("tests/{}", path.file_name().unwrap().to_str().unwrap());
        let text = read_to_string(&path).unwrap();
        let needs = text.contains(r#""..", "corpus""#) || text.contains("../LICENSE");
        assert_eq!(exclude.contains(&format!("\"{name}\"")), needs, "{name}");
    }
}

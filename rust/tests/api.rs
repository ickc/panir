//! The API: reading from bytes and readers, errors, the version, `Eq` and
//! `Hash`, visitors on a document.

use panir::{
    from_reader, from_slice, from_str, to_string, walk_inline_ref, Block, ColSpec, ColWidth,
    Inline, Pandoc, Table, Visit, PANDOC_API_VERSION,
};
use std::collections::HashSet;

const DOC: &str = r#"{"pandoc-api-version":[1,23,1,2],"meta":{},"blocks":[{"t":"Para","c":[{"t":"Str","c":"hi"}]}]}"#;

#[test]
fn from_bytes_and_readers() {
    let doc = from_str(DOC).unwrap();
    assert_eq!(from_slice(DOC.as_bytes()).unwrap(), doc);
    assert_eq!(from_reader(DOC.as_bytes()).unwrap(), doc);
    // text that isn't UTF-8: where, in lines and columns
    let mut bad = DOC.replace(",\"meta\"", "\n,\"meta\"").into_bytes();
    let at = bad.iter().position(|&b| b == b'h').unwrap();
    bad[at] = 0xff;
    let e = from_slice(&bad).unwrap_err();
    assert!(e.message.starts_with("not UTF-8: "), "{e}");
    assert_eq!(
        (e.path.as_str(), e.line, e.column),
        ("", 2, at - DOC.find(",\"meta\"").unwrap())
    );
    assert_eq!(from_reader(&bad[..]).unwrap_err(), e);
    // a reader that fails
    struct Failing;
    impl std::io::Read for Failing {
        fn read(&mut self, _: &mut [u8]) -> std::io::Result<usize> {
            Err(std::io::Error::other("no"))
        }
    }
    let e = from_reader(Failing).unwrap_err();
    assert_eq!(
        (e.to_string().as_str(), e.line),
        ("reading the document: no", 0)
    );
}

#[test]
fn an_errors_path_is_empty_for_the_document_never_a_dot() {
    for (json, path) in [
        ("[]", ""),
        (
            "{\"pandoc-api-version\":[1,23],\"meta\":{},\"blocks\":7}",
            "blocks",
        ),
        (
            "{\"pandoc-api-version\":[1,23],\"meta\":[],\"blocks\":[]}",
            "meta",
        ),
    ] {
        let e = from_str(json).unwrap_err();
        assert_eq!(e.path, path, "{e}");
        assert!(e.line > 0 && !e.message.contains(" at line "), "{e}");
    }
    let e = from_str("[]").unwrap_err();
    assert_eq!(e.to_string(), format!("{} at line 1 column 2", e.message));
}

#[test]
fn every_document_has_a_version_pandoc_reads() {
    assert_eq!(Pandoc::default().api_version(), PANDOC_API_VERSION);
    assert_eq!(from_str(DOC).unwrap().api_version(), PANDOC_API_VERSION);
    let old = DOC.replace("[1,23,1,2]", "[1,22,2]");
    let short = DOC.replace("[1,23,1,2]", "[]");
    for json in [&old, &short] {
        // through serde too, not only from_str
        let e = serde_json::from_str::<Pandoc>(json).unwrap_err();
        assert!(e.to_string().starts_with("expected 1.23.*, got"), "{e}");
        assert_eq!(from_str(json).unwrap_err().path, "pandoc-api-version");
    }
    // another 1.23 is kept as it was
    let other = DOC.replace("[1,23,1,2]", "[1,23]");
    assert_eq!(to_string(&from_str(&other).unwrap()), other);
}

#[test]
fn nodes_are_eq_and_hash() {
    let width = |w: f64| -> Block {
        Table {
            col_specs: vec![ColSpec {
                width: ColWidth::ColWidth(w),
                ..Default::default()
            }],
            ..Default::default()
        }
        .into()
    };
    // a width that isn't a number (pandoc's null) equals itself
    assert_eq!(width(f64::NAN), width(f64::NAN));
    assert_ne!(width(0.5), width(0.25));
    assert_ne!(width(0.0), width(-0.0)); // (written 0.0 and -0.0)
    let set: HashSet<Block> = [
        width(f64::NAN),
        width(f64::NAN),
        width(0.5),
        Block::HorizontalRule,
    ]
    .into_iter()
    .collect();
    assert_eq!(set.len(), 3);
    let docs: HashSet<Pandoc> = [from_str(DOC).unwrap(), from_str(DOC).unwrap()].into();
    assert_eq!(docs.len(), 1);
}

#[test]
fn a_document_visited() {
    struct Strs(usize);
    impl Visit for Strs {
        fn visit_inline(&mut self, x: &Inline) {
            self.0 += matches!(x, Inline::Str(_)) as usize;
            walk_inline_ref(self, x);
        }
    }
    let mut doc = from_str(DOC).unwrap();
    let mut n = Strs(0);
    doc.visit(&mut n);
    assert_eq!(n.0, 1);
    struct Twice;
    impl panir::VisitMut for Twice {
        fn visit_inlines(&mut self, xs: &mut Vec<Inline>) {
            xs.extend(xs.clone());
        }
    }
    doc.visit_mut(&mut Twice);
    doc.visit(&mut n);
    assert_eq!(n.0, 3);
}

#[test]
fn lists_are_read_to_their_length() {
    let doc = format!(
        r#"{{"pandoc-api-version":[1,23,1,2],"meta":{{}},"blocks":[{}]}}"#,
        [r#"{"t":"Para","c":[{"t":"Str","c":"a"},{"t":"Space"},{"t":"Emph","c":[{"t":"Str","c":"b"},{"t":"Space"},{"t":"Str","c":"c"}]},{"t":"Space"},{"t":"Str","c":"d"}]}"#; 5]
            .join(",")
    );
    struct Capacity(usize);
    impl panir::VisitMut for Capacity {
        fn visit_inlines(&mut self, xs: &mut Vec<Inline>) {
            self.0 += xs.capacity() - xs.len();
            panir::walk_inlines(self, xs);
        }
        fn visit_blocks(&mut self, xs: &mut Vec<Block>) {
            self.0 += xs.capacity() - xs.len();
            panir::walk_blocks(self, xs);
        }
    }
    let mut read = from_str(&doc).unwrap();
    let mut unused = Capacity(0);
    read.visit_mut(&mut unused);
    assert_eq!(unused.0, 0);
    // a list whose reading failed leaves nothing behind for the next
    let bad = doc.replacen(r#"{"t":"Str","c":"c"}"#, r#"{"t":"Str","c":7}"#, 1);
    assert_eq!(from_str(&bad).unwrap_err().path, "blocks[0].c[2].c[2].c");
    assert_eq!(from_str(&doc).unwrap(), read);
    assert_eq!(to_string(&read), doc);
}

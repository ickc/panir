//! The shared corpus: what every binding must accept and reject.

use panir::{from_str, to_string};
use std::path::PathBuf;

fn corpus(name: &str) -> Vec<String> {
    let path: PathBuf = [env!("CARGO_MANIFEST_DIR"), "..", "corpus", name]
        .iter()
        .collect();
    let text = std::fs::read_to_string(path).unwrap();
    // split on "\n" only: strings may hold other line separators
    text.split('\n')
        .filter(|l| !l.is_empty())
        .map(String::from)
        .collect()
}

#[test]
fn round_trip() {
    let mut docs = corpus("arbitrary.jsonl");
    docs.extend(corpus("pandoc.jsonl"));
    for (i, line) in docs.iter().enumerate() {
        let doc = from_str(line).unwrap_or_else(|e| panic!("document {i}: {e}"));
        // byte for byte: Rust writes keys in pandoc's order and doubles as
        // Haskell shows them (comparing parsed JSON would read both sides'
        // numbers with the same parser, and hide what it gets wrong)
        assert_eq!(&to_string(&doc), line, "document {i}");
        // the types' Serialize gives serde_json the same text, and a writer gets it
        assert_eq!(&serde_json::to_string(&doc).unwrap(), line, "document {i}");
        let mut written = Vec::new();
        panir::to_writer(&mut written, &doc).unwrap();
        assert_eq!(written, line.as_bytes(), "document {i}");
    }
}

#[test]
fn strings_are_escaped_as_serde_json_does() {
    let text: String = (0..0x80u8)
        .map(char::from)
        .chain("é\u{2028}\u{1f600}".chars())
        .collect();
    let doc = panir::Pandoc::new(vec![panir::Block::Para(vec![
        panir::Inline::Str(text.as_str().into()),
        panir::Inline::Str("".into()),
        panir::Inline::Str("\"\\\"".into()),
    ])]);
    let written = to_string(&doc);
    assert_eq!(written, serde_json::to_string(&doc).unwrap());
    assert!(from_str(&written).unwrap() == doc);
    // more than the 64 kB a writer gets at a time
    let big = panir::Pandoc::new(vec![doc.blocks[0].clone(); 2000]);
    let mut out = Vec::new();
    panir::to_writer(&mut out, &big).unwrap();
    assert_eq!(out, to_string(&big).into_bytes());
}

#[test]
fn doubles_read_exactly() {
    // 16 and 17 significant digits: serde_json reads these one unit in the
    // last place off without its float_roundtrip feature
    for width in [
        "0.9119595761642921",
        "0.12105451833716219",
        "3.9473684210526314e-2",
    ] {
        let doc = format!(
            r#"{{"pandoc-api-version":[1,23,1,2],"meta":{{}},"blocks":[{{"t":"Table","c":[["",[],[]],[null,[]],[[{{"t":"AlignDefault"}},{{"t":"ColWidth","c":{width}}}]],[["",[],[]],[]],[],[["",[],[]],[]]]}}]}}"#
        );
        assert_eq!(to_string(&from_str(&doc).unwrap()), doc);
    }
}

/// A JSON path as serde_path_to_error prints it: `blocks[0].c[1]`.
fn json_path(p: &serde_json::Value) -> String {
    let mut out = String::new();
    for seg in p.as_array().unwrap() {
        match seg {
            serde_json::Value::Number(n) => out += &format!("[{n}]"),
            serde_json::Value::String(k) if out.is_empty() => out += k,
            serde_json::Value::String(k) => out += &format!(".{k}"),
            _ => unreachable!(),
        }
    }
    out
}

#[test]
fn contents_before_tag() {
    // pandoc writes "t" first; other tools may not (sorted keys)
    let doc = r#"{"pandoc-api-version":[1,23,1],"meta":{},"blocks":[{"c":[{"c":42,"t":"Str"}],"t":"Para"}]}"#;
    let e = from_str(doc).unwrap_err();
    // the whole path, through each level read out of order
    assert_eq!(e.path, "blocks[0].c[0].c");
    assert_eq!(e.message, "invalid type: integer `42`, expected a string");
    // where the reader was: at the end of the outer node's tag
    assert_eq!((e.line, e.column), (1, 88));
    // and the next document's error has its own path
    let e = from_str(&doc.replace("42", "\"x\"").replace("\"Para\"", "\"Parx\"")).unwrap_err();
    assert_eq!(e.path, "blocks[0]");
    assert!(e.message.starts_with("unknown variant `Parx`"), "{e}");
}

#[test]
fn invalid() {
    for line in corpus("invalid.jsonl") {
        #[derive(serde::Deserialize)]
        struct Case<'a> {
            name: String,
            json_path: serde_json::Value,
            // as written: serde_json::Value would sort the keys, "c" before "t"
            #[serde(borrow)]
            document: &'a serde_json::value::RawValue,
        }
        let case: Case = serde_json::from_str(&line).unwrap();
        let doc = case.document.get();
        // the path of the value, or of its contents ("c") for a node whose
        // contents are the wrong shape
        let want = json_path(&case.json_path);
        match from_str(doc) {
            Ok(_) => panic!("accepted {}", case.name),
            Err(e) => {
                println!("{}: {e}", case.name);
                let got = e.path.clone();
                let with_c = if want.is_empty() {
                    "c".to_string()
                } else {
                    format!("{want}.c")
                };
                assert!(
                    got == want || got == with_c,
                    "{}: path {got}, expected {want}",
                    case.name
                );
            }
        }
    }
}

/// An error's path is found from its position in the text; a reading that
/// tracks every value's path (serde_path_to_error) must say the same, for
/// values of the wrong type anywhere in the corpus.
#[test]
fn an_errors_path_is_where_tracking_every_value_finds_it() {
    let mut seed = 12345u64;
    let mut rand = move |n: usize| {
        seed ^= seed << 13;
        seed ^= seed >> 7;
        seed ^= seed << 17;
        (seed % n as u64) as usize
    };
    const WRONG: [&str; 9] = [
        "null",
        "7",
        "\"x\"",
        "[]",
        "{}",
        "true",
        "[1,2,3,4,5,6,7]",
        "{\"t\":\"Bogus\"}",
        "1.5",
    ];
    let mut errors = 0;
    for line in corpus("arbitrary.jsonl") {
        // where the values that aren't lists or objects are (and empty lists)
        let b = line.as_bytes();
        let mut spans = Vec::new();
        let mut i = 0;
        while i < b.len() {
            match b[i] {
                b'"' => {
                    let start = i;
                    i += 1;
                    while b[i] != b'"' {
                        i += if b[i] == b'\\' { 2 } else { 1 };
                    }
                    i += 1;
                    if b.get(i) != Some(&b':') {
                        spans.push((start, i));
                    }
                }
                b'0'..=b'9' | b'-' | b't' | b'f' | b'n' => {
                    let start = i;
                    while i < b.len() && !matches!(b[i], b',' | b']' | b'}') {
                        i += 1;
                    }
                    spans.push((start, i));
                }
                b'[' if b.get(i + 1) == Some(&b']') => {
                    spans.push((i, i + 2));
                    i += 2;
                }
                _ => i += 1,
            }
        }
        for _ in 0..20 {
            let (start, end) = spans[rand(spans.len())];
            let wrong = WRONG[rand(WRONG.len())];
            let doc = format!("{}{}{}", &line[..start], wrong, &line[end..]);
            let Err(e) = from_str(&doc) else { continue };
            let mut de = serde_json::Deserializer::from_str(&doc);
            // (read as a document: only its version was wrong)
            let Err(tracked) = serde_path_to_error::deserialize::<_, panir::Pandoc>(&mut de) else {
                continue;
            };
            let tracked = tracked.path().to_string();
            assert_eq!(
                e.path,
                if tracked == "." { "" } else { &tracked },
                "{e}\n{doc}"
            );
            // the same with every node's contents before its tag (keys
            // sorted): the path goes on through each, read out of order
            let sorted = serde_json::from_str::<serde_json::Value>(&doc)
                .unwrap()
                .to_string();
            let s = from_str(&sorted).unwrap_err();
            assert_eq!((&s.path, &s.message), (&e.path, &e.message), "{sorted}");
            errors += 1;
        }
    }
    assert!(errors > 3000, "{errors}");
}

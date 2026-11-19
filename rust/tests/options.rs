//! A filter's options from a code block's YAML header and the document's
//! metadata: the split (corpus/yaml-blocks.jsonl, pandoc's verdicts),
//! metadata as plain data, the options, and yaml_filter.

use std::path::PathBuf;
use std::process::{Command, Stdio};

use panir::{
    Attr, Block, CodeBlock, Inline, Meta, MetaValue, Options, Pandoc, inlines, to_data, yaml_block,
    yaml_filter,
};
use serde_json::{Value, json};

#[test]
fn yaml_block_as_the_corpus() {
    let corpus: PathBuf = [env!("CARGO_MANIFEST_DIR"), "..", "corpus"]
        .iter()
        .collect();
    let text = std::fs::read_to_string(corpus.join("yaml-blocks.jsonl")).unwrap();
    let cases: Vec<Value> = text
        .split('\n')
        .filter(|l| !l.is_empty())
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    assert!(cases.len() > 30);
    for case in cases {
        let (header, data) = yaml_block(case["text"].as_str().unwrap());
        assert_eq!(header, case["header"].as_str(), "{}", case["name"]);
        assert_eq!(data, case["data"].as_str().unwrap(), "{}", case["name"]);
    }
}

fn map<const N: usize>(entries: [(&str, MetaValue); N]) -> Meta {
    entries.into_iter().map(|(k, v)| (k.into(), v)).collect()
}

#[test]
fn metadata_as_plain_data() {
    let meta = map([
        ("flag", false.into()),
        ("text", "10".into()),
        (
            "caption",
            vec![Inline::Emph(inlines("Sales")), Inline::Space, "by".into()].into(),
        ),
        ("body", vec![Block::from("a"), Block::from("b")].into()),
        (
            "list",
            vec!["1".into(), true.into(), MetaValue::MetaList(vec![])].into(),
        ),
        ("map", map([("k", inlines("v").into())]).into()),
    ]);
    let want = json!({
        "flag": false, "text": "10", "caption": "Sales by", "body": "ab",
        "list": ["1", true, []], "map": {"k": "v"},
    });
    assert_eq!(to_data(&meta), want);
    assert_eq!(to_data(&MetaValue::MetaMap(meta.clone())), want);
    assert_eq!(to_data(&Options(meta)), want);
}

#[test]
fn options_of() {
    let doc = map([
        (
            "pantable",
            map([
                ("caption", "doc".into()),
                ("alignment", "LC".into()),
                (
                    "csv",
                    map([("delimiter", ";".into()), ("x", "1".into())]).into(),
                ),
            ])
            .into(),
        ),
        ("other", map([("caption", "other's".into())]).into()),
    ]);
    let header = map([
        ("caption", inlines("block").into()),
        ("csv", map([("delimiter", ",".into())]).into()),
    ]);
    // a key's value replaced whole: no deeper merge
    assert_eq!(
        to_data(&Options::of(&doc, "pantable", Some(&header))),
        json!({"caption": "block", "alignment": "LC", "csv": {"delimiter": ","}})
    );
    let MetaValue::MetaMap(defaults) = &doc["pantable"] else {
        panic!()
    };
    assert_eq!(Options::of(&doc, "pantable", None).0, *defaults);
    assert_eq!(Options::of(&doc, "missing", Some(&header)).0, header);
    // under the name, anything but a map is ignored
    let text = map([("pantable", "x".into())]);
    assert_eq!(Options::of(&text, "pantable", None), Options::default());
}

#[test]
fn options_read() {
    let options = Options(map([
        (
            "caption",
            vec![Inline::Emph(inlines("Sales")), Inline::Space, "by".into()].into(),
        ),
        ("string", "x".into()),
        ("blocks", Block::from("p").into()),
        ("yes", true.into()),
        ("width", "0.4".into()),
        ("big", "1000".into()),
        ("fraction", "1/2".into()),
        ("ms", vec![MetaValue::from("1"), "2".into()].into()),
        ("csv", map([("delimiter", ";".into())]).into()),
    ]));
    assert!(matches!(
        options.get("caption"),
        Some(MetaValue::MetaInlines(_))
    ));
    assert_eq!(options.get("missing"), None);
    assert_eq!(options.text("caption").as_deref(), Some("Sales by"));
    assert_eq!(options.text("string").as_deref(), Some("x"));
    assert_eq!(options.text("blocks").as_deref(), Some("p"));
    assert_eq!(options.boolean("yes"), Some(true));
    assert_eq!(options.number("width"), Some(0.4));
    assert_eq!(options.number("big"), Some(1000.0));
    assert_eq!(options.number("fraction"), None);
    assert_eq!(options.list("ms"), Some(vec![json!("1"), json!("2")]));
    assert_eq!(
        options.map("csv").map(Value::Object),
        Some(json!({"delimiter": ";"}))
    );
    // of another shape, or missing: None
    assert_eq!(options.text("missing"), None);
    assert_eq!(options.boolean("missing"), None);
    assert_eq!(options.number("missing"), None);
    assert_eq!(options.list("missing"), None);
    assert_eq!(options.map("missing"), None);
    assert_eq!(options.text("yes"), None);
    assert_eq!(options.text("ms"), None);
    assert_eq!(options.boolean("string"), None);
    assert_eq!(options.number("yes"), None);
    assert_eq!(options.list("csv"), None);
    assert_eq!(options.map("ms"), None);
}

fn code(text: &str, classes: &[&str]) -> Block {
    Block::CodeBlock(Box::new(CodeBlock {
        attr: Attr {
            classes: classes.iter().map(|c| (*c).into()).collect(),
            ..Attr::default()
        },
        text: text.into(),
    }))
}

/// A reader that reads each header as its metadata from `known` (by its
/// text), or, if missing there, as pandoc reads a YAML scalar: blocks.
fn stub<'a>(
    known: &'a [(&'a str, Meta)],
    calls: &'a mut Vec<Vec<String>>,
) -> impl FnOnce(&[String]) -> Result<Vec<Pandoc>, String> + 'a {
    move |texts| {
        calls.push(texts.to_vec());
        Ok(texts
            .iter()
            .map(|t| match known.iter().find(|(k, _)| k == t) {
                Some((_, meta)) => {
                    let mut doc = Pandoc::new(vec![]);
                    doc.meta = meta.clone();
                    doc
                }
                None => Pandoc::new(vec![Block::from(t.as_str())]),
            })
            .collect())
    }
}

#[test]
fn yaml_filter_replaces_the_blocks() {
    let mut doc = Pandoc::new(vec![
        code("---\ncaption: one\n---\na,b\n", &["table"]),
        Block::Div(Box::new(panir::Div {
            attr: Attr::default(),
            content: vec![code("plain,data", &["table"])],
        })),
        code("---\ncaption: x\n---\nnot ours", &["other"]),
        code("---\ncaption\n---\nc,d", &["csv"]), // a scalar: no header
        code("---\n---\ne,f", &["table", "x"]),
    ]);
    doc.meta = map([(
        "pantable",
        map([("caption", "default".into()), ("alignment", "LC".into())]).into(),
    )]);
    let known = [
        (
            "---\ncaption: one\n---\n",
            map([("caption", inlines("one").into())]),
        ),
        ("---\n---\n", Meta::new()),
    ];
    let mut calls = vec![];
    let mut seen = vec![];
    yaml_filter(
        &mut doc,
        &["table", "csv"],
        "pantable",
        stub(&known, &mut calls),
        |options, data, block| {
            seen.push((
                to_data(options),
                data.to_owned(),
                block.attr.classes[0].to_string(),
            ));
            if data.starts_with("plain") {
                return Ok::<_, String>(Some(vec![]));
            }
            let caption = options.text("caption").unwrap_or_default();
            Ok(Some(vec![Block::from(caption.as_str()), Block::from(data)]))
        },
    )
    .unwrap();
    // one call, every header, in the document's order
    assert_eq!(
        calls,
        [[
            "---\ncaption: one\n---\n",
            "---\ncaption\n---\n",
            "---\n---\n"
        ]]
    );
    let defaults = json!({"caption": "default", "alignment": "LC"});
    assert_eq!(
        seen,
        [
            (
                json!({"caption": "one", "alignment": "LC"}),
                "a,b\n".into(),
                "table".into()
            ),
            (defaults.clone(), "plain,data".into(), "table".into()),
            (
                defaults.clone(),
                "---\ncaption\n---\nc,d".into(),
                "csv".into()
            ),
            (defaults, "e,f".into(), "table".into()),
        ]
    );
    assert_eq!(
        doc.blocks,
        [
            Block::from("one"),
            Block::from("a,b\n"),
            Block::Div(Box::new(panir::Div {
                attr: Attr::default(),
                content: vec![],
            })),
            code("---\ncaption: x\n---\nnot ours", &["other"]),
            Block::from("default"),
            Block::from("---\ncaption\n---\nc,d"),
            Block::from("default"),
            Block::from("e,f"),
        ]
    );
}

#[test]
fn yaml_filter_without_headers_reads_nothing() {
    let mut doc = Pandoc::new(vec![code("a,b", &["table"])]);
    let mut calls = vec![];
    yaml_filter(
        &mut doc,
        &["table"],
        "t",
        stub(&[], &mut calls),
        |_, _, _| Ok::<_, String>(None),
    )
    .unwrap();
    assert!(calls.is_empty());
    assert_eq!(doc.blocks, [code("a,b", &["table"])]);
}

#[test]
fn yaml_filter_errors() {
    let mut doc = Pandoc::new(vec![Block::Para(vec![]), code("---\n---\n", &["t"])]);
    let e = yaml_filter(
        &mut doc,
        &["t"],
        "t",
        |_| Err("no pandoc"),
        |_, _, _| Ok::<_, String>(None),
    )
    .unwrap_err();
    assert_eq!(e.to_string(), "no pandoc");
    let e = yaml_filter(
        &mut doc,
        &["t"],
        "t",
        |_| Ok::<_, String>(vec![Pandoc::new(vec![])]),
        |_, _, _| Err("bad table"),
    )
    .unwrap_err();
    assert_eq!(
        e.to_string(),
        "bad table\n  in filter function block, on the CodeBlock at blocks[1]"
    );
}

fn pandoc() -> Option<String> {
    let pandoc = std::env::var("PANDOC").unwrap_or_else(|_| "pandoc".into());
    match Command::new(&pandoc).arg("--version").output() {
        Ok(out) if out.status.success() => Some(pandoc),
        // in CI, a test that needs pandoc fails without it
        _ if std::env::var_os("CI").is_some() => panic!("no pandoc: set PANDOC"),
        _ => None,
    }
}

/// With the pandoc command as the reader: the header read as markdown.
#[test]
fn yaml_filter_with_pandoc() {
    let Some(pandoc) = pandoc() else {
        eprintln!("skipped: no pandoc");
        return;
    };
    let read = |texts: &[String]| -> Result<Vec<Pandoc>, String> {
        texts
            .iter()
            .map(|text| {
                let mut child = Command::new(&pandoc)
                    .args(["-f", "markdown", "-t", "json"])
                    .stdin(Stdio::piped())
                    .stdout(Stdio::piped())
                    .spawn()
                    .map_err(|e| e.to_string())?;
                let mut stdin = child.stdin.take().unwrap();
                std::io::Write::write_all(&mut stdin, text.as_bytes()).unwrap();
                drop(stdin);
                let out = child.wait_with_output().map_err(|e| e.to_string())?;
                panir::from_slice(&out.stdout).map_err(|e| e.to_string())
            })
            .collect()
    };
    let text =
        "---\ncaption: \"*Sales* by region\"\nwidth: 0.4\nn: 010\nmarkdown: true\n---\na,b\n";
    let mut doc = Pandoc::new(vec![code(text, &["table"])]);
    doc.meta = map([("pantable", map([("markdown", false.into())]).into())]);
    let mut got = vec![];
    yaml_filter(
        &mut doc,
        &["table"],
        "pantable",
        read,
        |options, data, _| {
            got.push((options.clone(), data.to_owned()));
            Ok::<_, String>(None)
        },
    )
    .unwrap();
    let [(options, data)] = &got[..] else {
        panic!()
    };
    assert_eq!(data, "a,b\n");
    let mut caption = vec![Inline::Emph(inlines("Sales")), Inline::Space];
    caption.extend(inlines("by region"));
    assert_eq!(
        options.get("caption"),
        Some(&MetaValue::MetaInlines(caption))
    );
    assert_eq!(options.text("caption").as_deref(), Some("Sales by region"));
    assert_eq!(options.number("width"), Some(0.4));
    assert_eq!(options.text("n").as_deref(), Some("10"));
    assert_eq!(options.boolean("markdown"), Some(true));
}

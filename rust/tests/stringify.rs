//! stringify, as pandoc's Lua `pandoc.utils.stringify`: checked against
//! pandoc itself (on both corpora and on metadata of every kind) where
//! pandoc can be run: `$PANDOC`, or `pandoc` on `PATH`.

use panir::{from_str, stringify, Pandoc};
use serde_json::{json, Value};
use std::path::PathBuf;
use std::process::{Command, Stdio};

/// Metadata of every kind, its keys not in order, and in maps; quotes,
/// notes, citations, code, math, raw breaks.
const MADE: &str = r#"{"pandoc-api-version":[1,23,1,2],"meta":{
"z":{"t":"MetaString","c":"zs"},
"b":{"t":"MetaBool","c":true},
"m":{"t":"MetaMap","c":{"y":{"t":"MetaString","c":"my"},"a":{"t":"MetaInlines","c":[{"t":"Str","c":"ma"}]},
  "f":{"t":"MetaBool","c":false},
  "l":{"t":"MetaList","c":[{"t":"MetaString","c":"l1"},{"t":"MetaBlocks","c":[{"t":"Para","c":[{"t":"Str","c":"l2"}]}]}]}}},
"i":{"t":"MetaInlines","c":[{"t":"Str","c":"in"},{"t":"Note","c":[{"t":"Para","c":[{"t":"Str","c":"note"}]}]},{"t":"RawInline","c":["html","<br/>"]}]},
"k":{"t":"MetaBlocks","c":[{"t":"Para","c":[{"t":"Str","c":"bl"}]}]}},
"blocks":[{"t":"Para","c":[{"t":"Str","c":"body"},{"t":"LineBreak"},{"t":"Quoted","c":[{"t":"DoubleQuote"},[{"t":"Str","c":"q"}]]}]},
{"t":"Para","c":[{"t":"Cite","c":[[{"citationId":"k","citationPrefix":[{"t":"Str","c":"pre"}],"citationSuffix":[{"t":"Str","c":"suf"}],"citationMode":{"t":"NormalCitation"},"citationNoteNum":1,"citationHash":0}],[{"t":"Str","c":"cite"}]]},
  {"t":"Space"},{"t":"Code","c":[["",[],[]],"co"]},{"t":"Math","c":[{"t":"InlineMath"},"x"]},{"t":"RawInline","c":["HTML","<br>"]},
  {"t":"Quoted","c":[{"t":"SingleQuote"},[{"t":"Emph","c":[{"t":"Str","c":"s"}]}]]}]}]}"#;

/// For a document: its text, its metadata's, each metadata value's, each
/// block's (as the Lua below).
fn ours(doc: &Pandoc) -> Value {
    let meta: serde_json::Map<String, Value> = doc
        .meta
        .iter()
        .map(|(k, v)| (k.to_string(), stringify(v).into()))
        .collect();
    let blocks: Vec<String> = doc.blocks.iter().map(stringify).collect();
    json!({"whole": stringify(doc), "metamap": stringify(&doc.meta), "meta": meta, "blocks": blocks})
}

const LUA: &str = r#"
for line in io.lines() do
  local doc = pandoc.read(line, "json")
  local out = {whole = pandoc.utils.stringify(doc), metamap = pandoc.utils.stringify(doc.meta),
    meta = {}, blocks = {}}
  for k, v in pairs(doc.meta) do out.meta[k] = pandoc.utils.stringify(v) end
  for i, b in ipairs(doc.blocks) do out.blocks[i] = pandoc.utils.stringify(b) end
  print(pandoc.json.encode(out))
end
"#;

#[test]
fn metadata_of_every_kind() {
    // a map in the order of its keys; a whole document is its inlines'
    // text only (no MetaString, no MetaBool), but metadata stringified has
    // its strings and booleans too
    let doc = from_str(&MADE.replace('\n', "")).unwrap();
    assert_eq!(
        ours(&doc),
        json!({
            "whole": "in blmal2body “q”cite cox‘s’",
            "metamap": "truein blmafalsel1l2myzs",
            "meta": {"z": "zs", "b": "true", "m": "mafalsel1l2my", "i": "in ", "k": "bl"},
            "blocks": ["body “q”", "cite cox‘s’"],
        })
    );
    // lists of nodes, a citation (its prefix and suffix)
    let panir::Block::Para(xs) = &doc.blocks[1] else {
        panic!()
    };
    assert_eq!(stringify(xs), "cite cox‘s’");
    assert_eq!(stringify(&xs[1..3]), " co");
    let panir::Inline::Cite(c) = &xs[0] else {
        panic!()
    };
    assert_eq!(stringify(&c.citations), "presuf");
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

#[test]
fn as_pandoc() {
    let Some(pandoc) = pandoc() else {
        eprintln!("skipped: no pandoc");
        return;
    };
    let corpus = |name: &str| {
        let path: PathBuf = [env!("CARGO_MANIFEST_DIR"), "..", "corpus", name]
            .iter()
            .collect();
        std::fs::read_to_string(path).unwrap()
    };
    let mut texts: Vec<String> = vec![MADE.replace('\n', "")];
    for name in ["arbitrary.jsonl", "pandoc.jsonl"] {
        texts.extend(
            corpus(name)
                .split('\n')
                .filter(|l| !l.is_empty())
                .map(String::from),
        );
    }
    let mut child = Command::new(pandoc)
        .args(["lua", "-e", LUA])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let input = texts.join("\n") + "\n";
    let mut stdin = child.stdin.take().unwrap();
    let writer =
        std::thread::spawn(move || std::io::Write::write_all(&mut stdin, input.as_bytes()));
    let out = child.wait_with_output().unwrap();
    writer.join().unwrap().unwrap();
    assert!(out.status.success());
    let want: Vec<Value> = String::from_utf8(out.stdout)
        .unwrap()
        .split('\n')
        .filter(|l| !l.is_empty())
        .map(|l| {
            let mut w: Value = serde_json::from_str(l).unwrap();
            // (Lua's empty table is written {})
            for (key, empty) in [("blocks", json!([])), ("meta", json!({}))] {
                if w[key] == json!({}) || w[key] == json!([]) {
                    w[key] = empty;
                }
            }
            w
        })
        .collect();
    assert_eq!(want.len(), texts.len());
    let mut differ = 0;
    for (i, (text, want)) in texts.iter().zip(&want).enumerate() {
        let got = ours(&from_str(text).unwrap());
        if &got != want {
            differ += 1;
            eprintln!("document {i}:\n  pandoc {want}\n  panir  {got}");
        }
    }
    assert_eq!(differ, 0, "of {} documents", texts.len());
}

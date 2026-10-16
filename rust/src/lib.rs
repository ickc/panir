//! pandoc's document AST, generated from pandoc-types.
//!
//! The types (`Pandoc`, `Block`, `Inline`, ...) encode to and from pandoc's
//! JSON with serde: `{"t": "Str", "c": "x"}` is `Inline::Str("x")`.
//! [`Filter`] and [`apply`] run filters as pandoc runs Lua filters, in one of
//! three orders; `VisitMut` walks a document any other way. `filter` runs a
//! function as a pandoc JSON filter (`pandoc --filter`).
//!
//! ```
//! use panir::{Block, Inline, Pandoc};
//!
//! let doc: Pandoc = panir::from_str(
//!     r#"{"pandoc-api-version":[1,23,1],"meta":{},"blocks":[{"t":"Para","c":[{"t":"Str","c":"hi"}]}]}"#,
//! ).unwrap();
//! assert_eq!(doc.blocks, vec![Block::Para(vec![Inline::Str("hi".into())])]);
//! ```

mod filter;
mod generated;
mod text;

pub use filter::{apply, apply_with, Bottomup, Conversion, Ctx, Filter, Order, Topdown, Typewise};
pub use generated::*;
pub use text::Text;

use std::fmt;
use std::io::{Read, Write};

/// JSON that isn't a pandoc document of this API version.
#[derive(Debug)]
pub struct Error {
    /// Where, in the JSON: e.g. `blocks[0].c[1]`.
    pub path: String,
    pub message: String,
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.path.is_empty() || self.path == "." {
            write!(f, "{}", self.message)
        } else {
            write!(f, "{}: {}", self.path, self.message)
        }
    }
}

impl std::error::Error for Error {}

/// A document from pandoc's JSON, checking its `pandoc-api-version`.
pub fn from_str(json: &str) -> Result<Pandoc, Error> {
    // Read untracked (tracking each value's path made reading 30-40% slower);
    // only if that fails, read again to find the error's path.
    let doc: Pandoc = match serde_json::from_str(json) {
        Ok(doc) => doc,
        Err(first) => {
            let de = &mut serde_json::Deserializer::from_str(json);
            return Err(match serde_path_to_error::deserialize::<_, Pandoc>(de) {
                Err(e) => Error {
                    path: e.path().to_string(),
                    message: e.inner().to_string(),
                },
                // the document reads, but more follows it
                Ok(_) => Error {
                    path: String::new(),
                    message: first.to_string(),
                },
            });
        }
    };
    if doc.api_version.get(..2) != Some(&PANDOC_API_VERSION[..2]) {
        return Err(Error {
            path: "pandoc-api-version".into(),
            message: format!(
                "expected {}.{}.*, got {:?}",
                PANDOC_API_VERSION[0], PANDOC_API_VERSION[1], doc.api_version
            ),
        });
    }
    Ok(doc)
}

/// A `Double` as pandoc's JSON (aeson) writes it, as Haskell shows it:
/// `0.5`, `1.0`, and outside 0.1 to 10^7 with an exponent, `5.0e-2`.
fn haskell_double(x: f64) -> String {
    if !x.is_finite() {
        return "null".into();
    }
    let a = x.abs();
    if a == 0.0 || (0.1..1e7).contains(&a) {
        let t = format!("{x}");
        if t.contains('.') {
            t
        } else {
            t + ".0"
        }
    } else {
        let t = format!("{x:e}");
        match t.split_once('e') {
            Some((m, e)) if !m.contains('.') => format!("{m}.0e{e}"),
            _ => t,
        }
    }
}

#[doc(hidden)]
pub fn aeson_double<S: serde::Serializer>(x: &f64, s: S) -> Result<S::Ok, S::Error> {
    let raw = serde_json::value::RawValue::from_string(haskell_double(*x))
        .map_err(serde::ser::Error::custom)?;
    serde::Serialize::serialize(&raw, s)
}

/// A document as pandoc's JSON.
pub fn to_string(doc: &Pandoc) -> String {
    serde_json::to_string(doc).expect("the AST always encodes")
}

/// Reads `{"t": tag}` for an enum-like type, checking the tag.
#[doc(hidden)]
pub fn de_tag<'de, D: serde::Deserializer<'de>>(
    d: D,
    name: &'static str,
    tags: &'static [&'static str],
) -> Result<&'static str, D::Error> {
    use serde::de::{self, MapAccess, Visitor};
    struct V(&'static str, &'static [&'static str]);
    impl<'de> Visitor<'de> for V {
        type Value = &'static str;
        fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
            write!(f, "a {}: {{\"t\": ...}}", self.0)
        }
        fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<&'static str, A::Error> {
            let mut tag = None;
            while let Some(key) = map.next_key::<Key>()? {
                if key == Key::T {
                    tag = Some(Tag::check(map.next_value_seed(Tag(self.1))?, self.1)?);
                } else {
                    map.next_value::<de::IgnoredAny>()?;
                }
            }
            tag.ok_or_else(|| de::Error::missing_field("t"))
        }
    }
    d.deserialize_map(V(name, tags))
}

/// A key of a node's JSON object, `"t"`, `"c"` or another, matched without
/// copying it.
#[doc(hidden)]
#[derive(PartialEq, Eq)]
pub enum Key {
    T,
    C,
    Other,
}

impl<'de> serde::Deserialize<'de> for Key {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl serde::de::Visitor<'_> for V {
            type Value = Key;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("a key")
            }
            fn visit_str<E: serde::de::Error>(self, s: &str) -> Result<Key, E> {
                Ok(match s {
                    "t" => Key::T,
                    "c" => Key::C,
                    _ => Key::Other,
                })
            }
        }
        d.deserialize_identifier(V)
    }
}

/// A node's tag, one of these, matched without copying it; another is
/// returned as `Err`, for the error to be raised at the node (its path).
#[doc(hidden)]
pub struct Tag(pub &'static [&'static str]);

impl Tag {
    /// The tag, or an unknown variant error.
    pub fn check<E: serde::de::Error>(
        t: Result<&'static str, Text>,
        tags: &'static [&'static str],
    ) -> Result<&'static str, E> {
        t.map_err(|t| E::unknown_variant(&t, tags))
    }
}

impl<'de> serde::de::DeserializeSeed<'de> for Tag {
    type Value = Result<&'static str, Text>;
    fn deserialize<D: serde::Deserializer<'de>>(self, d: D) -> Result<Self::Value, D::Error> {
        struct V(&'static [&'static str]);
        impl serde::de::Visitor<'_> for V {
            type Value = Result<&'static str, Text>;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("a tag")
            }
            fn visit_str<E: serde::de::Error>(self, s: &str) -> Result<Self::Value, E> {
                Ok(self
                    .0
                    .iter()
                    .find(|t| **t == s)
                    .copied()
                    .ok_or_else(|| s.into()))
            }
        }
        d.deserialize_str(V(self.0))
    }
}

/// A string's words and spaces, as pandoc's Lua `pandoc.Inlines` (and
/// pandoc-types' `text`): `Str` for each run of non-spaces, and for each run
/// of spaces `SoftBreak` if it has a newline, else `Space`.
///
/// ```
/// use panir::{inlines, Block, Inline};
///
/// let p = Block::Para(inlines("hello world"));
/// assert_eq!(p, Block::Para(vec!["hello".into(), Inline::Space, "world".into()]));
/// ```
pub fn inlines(text: &str) -> Vec<Inline> {
    let is_space = |c: char| matches!(c, ' ' | '\t' | '\n' | '\r');
    let mut out = Vec::new();
    let mut rest = text;
    while let Some(c) = rest.chars().next() {
        let space = is_space(c);
        let end = rest.find(|c| is_space(c) != space).unwrap_or(rest.len());
        let (run, tail) = rest.split_at(end);
        out.push(if !space {
            Inline::Str(run.into())
        } else if run.contains(['\n', '\r']) {
            Inline::SoftBreak
        } else {
            Inline::Space
        });
        rest = tail;
    }
    out
}

/// A string as blocks, as pandoc's Lua `pandoc.Blocks`: one `Plain` of its
/// words and spaces. (To parse markup, use libpandoc.)
pub fn blocks(text: &str) -> Vec<Block> {
    vec![Block::Plain(inlines(text))]
}

/// A string where one inline goes is a `Str`, as in pandoc's Lua.
impl From<&str> for Inline {
    fn from(s: &str) -> Self {
        Inline::Str(s.into())
    }
}

impl From<String> for Inline {
    fn from(s: String) -> Self {
        Inline::Str(s.into())
    }
}

impl From<Text> for Inline {
    fn from(s: Text) -> Self {
        Inline::Str(s)
    }
}

/// A string where one block goes is `Plain` text, as in pandoc's Lua.
impl From<&str> for Block {
    fn from(s: &str) -> Self {
        Block::Plain(inlines(s))
    }
}

impl From<String> for Block {
    fn from(s: String) -> Self {
        Block::Plain(inlines(&s))
    }
}

impl Pandoc {
    /// A document with these blocks and no metadata.
    pub fn new(blocks: Vec<Block>) -> Self {
        Pandoc {
            blocks,
            ..Default::default()
        }
    }

    /// Visit the document with a visitor.
    pub fn visit<V: VisitMut + ?Sized>(&mut self, v: &mut V) {
        v.visit_pandoc(self);
    }
}

/// Run `f` as a pandoc JSON filter: read a document from stdin, change it,
/// write it to stdout. `f` gets the output format pandoc passes (the first
/// argument), if any. Errors go to stderr and exit with status 1.
///
/// ```no_run
/// panir::filter(|doc, _format| {
///     doc.blocks.retain(|b| !matches!(b, panir::Block::HorizontalRule));
/// });
/// ```
pub fn filter<F: FnOnce(&mut Pandoc, Option<&str>)>(f: F) {
    filter_with(|doc, conversion| f(doc, conversion.format.as_deref()));
}

/// As [`filter`], with the whole [`Conversion`] pandoc tells a JSON filter
/// ([`Conversion::from_env`]).
///
/// ```no_run
/// use panir::Conversion;
/// panir::filter_with(|doc, conversion: &Conversion| {
///     let columns = conversion.reader_options.as_ref().and_then(|o| o["columns"].as_u64());
///     let _ = columns;
/// });
/// ```
pub fn filter_with<F: FnOnce(&mut Pandoc, &Conversion)>(f: F) {
    let conversion = Conversion::from_env();
    let mut input = String::new();
    let result = std::io::stdin()
        .read_to_string(&mut input)
        .map_err(|e| e.to_string())
        .and_then(|_| from_str(&input).map_err(|e| e.to_string()));
    match result {
        Ok(mut doc) => {
            f(&mut doc, &conversion);
            let mut out = std::io::stdout().lock();
            out.write_all(to_string(&doc).as_bytes())
                .expect("writing stdout");
        }
        Err(e) => {
            eprintln!("panir filter: {e}");
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod double_tests {
    use super::haskell_double as h;

    #[test]
    fn as_haskell_shows() {
        assert_eq!(h(0.5), "0.5");
        assert_eq!(h(1.0), "1.0");
        assert_eq!(h(0.1), "0.1");
        assert_eq!(h(0.05), "5.0e-2");
        assert_eq!(h(0.039473684210526314), "3.9473684210526314e-2");
        assert_eq!(h(1e7), "1.0e7");
        assert_eq!(h(1234567.5), "1234567.5");
        assert_eq!(h(0.0), "0.0");
        assert_eq!(h(-0.25), "-0.25");
    }
}

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

/// How deep a document may be to be read: the levels of `Block`, `Inline`
/// and `MetaValue` in each other (a `Str` in an `Emph` in a `Para` in a `Div`
/// is four). A deeper one is an error, not a crash: a reader calls itself for
/// each level, and the stack is not endless.
///
/// It was 128 levels of JSON, serde_json's own limit: 42 nested divs, which a
/// web page read from HTML can have.
pub const MAX_DEPTH: usize = 256;

/// The stack a document may take to be read, in bytes: what a level takes
/// goes from 1 kB (a block quote) to 6 (a table in a table's cell), and five
/// times that unoptimized, so that [`MAX_DEPTH`] alone doesn't keep within
/// the megabyte a thread has on Windows, or a wasm module.
const MAX_STACK: usize = 640 << 10;

thread_local! {
    // the levels being read, and where the stack was at the first
    static DEPTH: std::cell::Cell<(usize, usize)> = const { std::cell::Cell::new((0, 0)) };
    // whether a level was refused since `from_str` began
    static TOO_DEEP: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// A level of the AST being read; counted while it lives.
#[doc(hidden)]
pub struct Level(());

impl Level {
    #[inline(never)]
    pub fn enter<E: serde::de::Error>() -> Result<Level, E> {
        let here = 0u8;
        let here = std::hint::black_box(&here) as *const u8 as usize;
        DEPTH.with(|d| {
            let (levels, first) = d.get();
            let first = if levels == 0 { here } else { first };
            let refused = if levels >= MAX_DEPTH {
                format!("nested more than {MAX_DEPTH} levels deep")
            } else if first.abs_diff(here) > MAX_STACK {
                format!("nested too deep for the stack ({levels} levels)")
            } else {
                d.set((levels + 1, first));
                return Ok(Level(()));
            };
            TOO_DEEP.with(|t| t.set(true));
            Err(E::custom(refused))
        })
    }
}

impl Drop for Level {
    fn drop(&mut self) {
        DEPTH.with(|d| {
            let (levels, first) = d.get();
            d.set((levels - 1, first));
        });
    }
}

/// `json` as a `T`, whole, to any depth of JSON ([`Level`] counts the AST's).
fn read<'a, T: serde::Deserialize<'a>>(json: &'a str) -> serde_json::Result<T> {
    let mut de = serde_json::Deserializer::from_str(json);
    de.disable_recursion_limit();
    let value = T::deserialize(&mut de)?;
    de.end()?;
    Ok(value)
}

/// A document from pandoc's JSON, checking its `pandoc-api-version`.
pub fn from_str(json: &str) -> Result<Pandoc, Error> {
    let wrong_version = |got: &[i64]| Error {
        path: "pandoc-api-version".into(),
        message: format!(
            "expected {}.{}.*, got {:?}",
            PANDOC_API_VERSION[0], PANDOC_API_VERSION[1], got
        ),
    };
    let right = |v: &[i64]| v.get(..2) == Some(&PANDOC_API_VERSION[..2]);
    // Read untracked (tracking each value's path made reading 30-40% slower);
    // only if that fails, read again to find the error's path.
    TOO_DEEP.with(|t| t.set(false));
    let doc: Pandoc = match read(json) {
        Ok(doc) => doc,
        Err(first) => {
            // another version's document: say that, not where it first differs
            #[derive(serde::Deserialize)]
            struct Version {
                #[serde(rename = "pandoc-api-version")]
                version: Vec<i64>,
            }
            if let Ok(v) = read::<Version>(json) {
                if !right(&v.version) {
                    return Err(wrong_version(&v.version));
                }
            }
            let whole = Error {
                path: String::new(),
                message: first.to_string(),
            };
            // Too deep: the reading for the path takes more stack for a
            // level, and would stop before; and such a path says little.
            if TOO_DEEP.with(|t| t.get()) {
                return Err(whole);
            }
            let mut de = serde_json::Deserializer::from_str(json);
            de.disable_recursion_limit();
            return Err(
                match serde_path_to_error::deserialize::<_, Pandoc>(&mut de) {
                    // (it stopped before the error, for its own stack)
                    Err(_) if TOO_DEEP.with(|t| t.get()) => whole,
                    Err(e) => Error {
                        path: e.path().to_string(),
                        message: e.inner().to_string(),
                    },
                    // the document reads, but more follows it
                    Ok(_) => whole,
                },
            );
        }
    };
    if !right(&doc.api_version) {
        return Err(wrong_version(&doc.api_version));
    }
    Ok(doc)
}

/// A finite `Double` as pandoc's JSON (aeson) writes it, as Haskell shows
/// it: `0.5`, `1.0`, and outside 0.1 to 10^7 with an exponent, `5.0e-2`.
fn haskell_double(x: f64) -> String {
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

/// A `Double` as pandoc writes it: one that isn't finite has no JSON number,
/// and is `null` (not a number) or `"+inf"`, `"-inf"`.
#[doc(hidden)]
pub fn aeson_double<S: serde::Serializer>(x: &f64, s: S) -> Result<S::Ok, S::Error> {
    if x.is_nan() {
        return s.serialize_unit();
    }
    if x.is_infinite() {
        return s.serialize_str(if *x > 0.0 { "+inf" } else { "-inf" });
    }
    let raw = serde_json::value::RawValue::from_string(haskell_double(*x))
        .map_err(serde::ser::Error::custom)?;
    serde::Serialize::serialize(&raw, s)
}

/// A `Double` as pandoc reads it: a number, or what [`aeson_double`] writes
/// for one that isn't finite.
#[doc(hidden)]
pub struct Double(pub f64);

impl<'de> serde::Deserialize<'de> for Double {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl serde::de::Visitor<'_> for V {
            type Value = Double;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("a number")
            }
            fn visit_f64<E>(self, x: f64) -> Result<Double, E> {
                Ok(Double(x))
            }
            fn visit_i64<E>(self, x: i64) -> Result<Double, E> {
                Ok(Double(x as f64))
            }
            fn visit_u64<E>(self, x: u64) -> Result<Double, E> {
                Ok(Double(x as f64))
            }
            fn visit_unit<E>(self) -> Result<Double, E> {
                Ok(Double(f64::NAN))
            }
            fn visit_str<E: serde::de::Error>(self, s: &str) -> Result<Double, E> {
                match s {
                    "+inf" => Ok(Double(f64::INFINITY)),
                    "-inf" => Ok(Double(f64::NEG_INFINITY)),
                    _ => Err(E::invalid_type(serde::de::Unexpected::Str(s), &self)),
                }
            }
        }
        d.deserialize_any(V)
    }
}

/// Reads any JSON as a `serde_json::Value`, counting its levels as the
/// AST's are ([`Level`]): the contents of a node that come before its tag
/// (not pandoc's order) wait in one until the tag says what they are.
#[doc(hidden)]
pub struct Early;

impl<'de> serde::de::DeserializeSeed<'de> for Early {
    type Value = serde_json::Value;
    fn deserialize<D: serde::Deserializer<'de>>(self, d: D) -> Result<Self::Value, D::Error> {
        use serde::de::{MapAccess, SeqAccess, Visitor};
        use serde_json::Value;
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Value;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("JSON")
            }
            fn visit_bool<E>(self, x: bool) -> Result<Value, E> {
                Ok(Value::Bool(x))
            }
            fn visit_i64<E>(self, x: i64) -> Result<Value, E> {
                Ok(x.into())
            }
            fn visit_u64<E>(self, x: u64) -> Result<Value, E> {
                Ok(x.into())
            }
            fn visit_f64<E>(self, x: f64) -> Result<Value, E> {
                Ok(serde_json::Number::from_f64(x).map_or(Value::Null, Value::Number))
            }
            fn visit_str<E>(self, x: &str) -> Result<Value, E> {
                Ok(Value::String(x.to_owned()))
            }
            fn visit_string<E>(self, x: String) -> Result<Value, E> {
                Ok(Value::String(x))
            }
            fn visit_unit<E>(self) -> Result<Value, E> {
                Ok(Value::Null)
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Value, A::Error> {
                let _level = Level::enter()?;
                let mut out = Vec::new();
                while let Some(x) = seq.next_element_seed(Early)? {
                    out.push(x);
                }
                Ok(Value::Array(out))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Value, A::Error> {
                let _level = Level::enter()?;
                let mut out = serde_json::Map::new();
                while let Some(k) = map.next_key::<String>()? {
                    out.insert(k, map.next_value_seed(Early)?);
                }
                Ok(Value::Object(out))
            }
        }
        d.deserialize_any(V)
    }
}

/// A document as pandoc's JSON.
pub fn to_string(doc: &Pandoc) -> String {
    serde_json::to_string(doc).expect("the AST always encodes")
}

/// Write a document as pandoc's JSON.
pub fn to_writer<W: Write>(writer: W, doc: &Pandoc) -> std::io::Result<()> {
    serde_json::to_writer(writer, doc).map_err(std::io::Error::from)
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
    let run = || -> Result<(), String> {
        let mut input = String::new();
        std::io::stdin()
            .read_to_string(&mut input)
            .map_err(|e| format!("reading the document: {e}"))?;
        let mut doc = from_str(&input).map_err(|e| e.to_string())?;
        drop(input);
        f(&mut doc, &conversion);
        let mut out = std::io::BufWriter::new(std::io::stdout().lock());
        to_writer(&mut out, &doc)
            .and_then(|()| out.flush())
            .map_err(|e| format!("writing the document: {e}"))
    };
    if let Err(e) = run() {
        eprintln!("panir filter: {e}");
        std::process::exit(1);
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
        assert_eq!(h(5e-324), "5.0e-324");
        assert_eq!(h(1.7976931348623157e308), "1.7976931348623157e308");
    }
}

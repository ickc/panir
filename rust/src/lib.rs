//! pandoc's document AST, generated from pandoc-types.
//!
//! The types (`Pandoc`, `Block`, `Inline`, ...) encode to and from pandoc's
//! JSON with serde: `{"t": "Str", "c": "x"}` is `Inline::Str("x")`.
//! [`Filter`] and [`apply`] run filters as pandoc runs Lua filters, in one of
//! three orders, on a document or a fragment of one; [`VisitMut`] and
//! [`Visit`] walk a document any other way. [`filter`] runs a function as a
//! pandoc JSON filter (`pandoc --filter`); [`stringify()`] is pandoc's.
//!
//! ```
//! use panir::{Block, Inline, Pandoc};
//!
//! let doc: Pandoc = panir::from_str(
//!     r#"{"pandoc-api-version":[1,23,1],"meta":{},"blocks":[{"t":"Para","c":[{"t":"Str","c":"hi"}]}]}"#,
//! ).unwrap();
//! assert_eq!(doc.blocks, vec![Block::Para(vec![Inline::Str("hi".into())])]);
//! ```

#![forbid(unsafe_code)]

mod filter;
mod generated;
mod stringify;
mod text;

// the README's examples compile and run as the docs' do
#[cfg(doctest)]
#[doc = include_str!("../README.md")]
struct Readme;

pub use filter::{apply, apply_with, Conversion, Ctx, Filter, Order, Walkable};
pub use generated::*;
pub use stringify::{stringify, Stringify};
pub use text::Text;

use std::fmt;
use std::io::{Read, Write};

/// JSON that isn't a pandoc document of this API version (or, from
/// [`from_reader`], a document that couldn't be read).
///
/// ```
/// let e = panir::from_str(r#"{"pandoc-api-version":[1,23,1],"meta":{},"blocks":[{"t":"Para","c":7}]}"#)
///     .unwrap_err();
/// assert_eq!(e.path, "blocks[0].c");
/// assert_eq!(e.message, "invalid type: integer `7`, expected a sequence");
/// assert_eq!((e.line, e.column), (1, 68));
/// assert_eq!(e.to_string(), format!("blocks[0].c: {} at line 1 column 68", e.message));
/// ```
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error {
    /// Where in the JSON, as `blocks[0].c[1]` or `meta.title`: empty for the
    /// document itself, or where nothing more can be said (a document too
    /// deep, text that isn't UTF-8).
    pub path: String,
    /// What is wrong, without where.
    pub message: String,
    /// The line and column in the text (from 1) where the reader was, as
    /// serde_json counts them; 0 when the error has no place in the text.
    pub line: usize,
    pub column: usize,
}

impl Error {
    fn new(path: &str, message: String) -> Self {
        Error {
            path: path.into(),
            message,
            line: 0,
            column: 0,
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if !self.path.is_empty() {
            write!(f, "{}: ", self.path)?;
        }
        f.write_str(&self.message)?;
        if self.line > 0 {
            write!(f, " at line {} column {}", self.line, self.column)?;
        }
        Ok(())
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
    // the path, inside contents read before their tag, of the error being
    // raised (`contents`): from the node, which `from_str` finds
    static INNER: std::cell::Cell<String> = const { std::cell::Cell::new(String::new()) };
}

/// A level of the AST being read; counted while it lives.
pub(crate) struct Level(());

impl Level {
    #[inline]
    pub(crate) fn enter<E: serde::de::Error>() -> Result<Level, E> {
        let here = 0u8;
        let here = std::hint::black_box(&here) as *const u8 as usize;
        DEPTH.with(|d| {
            let (levels, first) = d.get();
            let first = if levels == 0 { here } else { first };
            if levels < MAX_DEPTH && first.abs_diff(here) <= MAX_STACK {
                d.set((levels + 1, first));
                return Ok(Level(()));
            }
            Err(E::custom(Self::refused(levels)))
        })
    }

    #[cold]
    fn refused(levels: usize) -> String {
        TOO_DEEP.with(|t| t.set(true));
        if levels >= MAX_DEPTH {
            format!("nested more than {MAX_DEPTH} levels deep")
        } else {
            format!("nested too deep for the stack ({levels} levels)")
        }
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
    TOO_DEEP.with(|t| t.set(false));
    INNER.with(|p| p.set(String::new()));
    read(json).map_err(|e| {
        // another version's document: say that, not where it first differs
        #[derive(serde::Deserialize)]
        struct Version {
            #[serde(rename = "pandoc-api-version")]
            version: Vec<i64>,
        }
        if let Ok(v) = read::<Version>(json) {
            if !right_version(&v.version) {
                return Error::new("pandoc-api-version", wrong_version(&v.version));
            }
        }
        // (of a document too deep, a path says little, at length)
        let mut path = String::new();
        if !TOO_DEEP.with(std::cell::Cell::get) {
            path = path_at(json, &e);
            join(&mut path, &INNER.with(std::cell::Cell::take));
        }
        Error {
            path,
            message: message(&e),
            line: e.line(),
            column: e.column(),
        }
    })
}

/// A document from pandoc's JSON as bytes, which must be UTF-8.
pub fn from_slice(json: &[u8]) -> Result<Pandoc, Error> {
    let text = std::str::from_utf8(json).map_err(|e| {
        let before = &json[..e.valid_up_to()];
        let start = before
            .iter()
            .rposition(|&b| b == b'\n')
            .map_or(0, |i| i + 1);
        Error {
            line: before.iter().filter(|&&b| b == b'\n').count() + 1,
            column: before.len() - start + 1,
            ..Error::new("", format!("not UTF-8: {e}"))
        }
    })?;
    from_str(text)
}

/// A document from pandoc's JSON, read whole from `reader` first.
pub fn from_reader<R: Read>(mut reader: R) -> Result<Pandoc, Error> {
    let mut json = Vec::new();
    reader
        .read_to_end(&mut json)
        .map_err(|e| Error::new("", format!("reading the document: {e}")))?;
    from_slice(&json)
}

/// Whether a `pandoc-api-version` is one pandoc reads as this crate's: the
/// same major and minor version.
fn right_version(v: &[i64]) -> bool {
    v.get(..2) == Some(&PANDOC_API_VERSION[..2])
}

fn wrong_version(got: &[i64]) -> String {
    format!(
        "expected {}.{}.*, got {:?}",
        PANDOC_API_VERSION[0], PANDOC_API_VERSION[1], got
    )
}

/// Reads a `Pandoc`'s `pandoc-api-version`: one pandoc reads as this
/// crate's, so that every `Pandoc` writes one.
pub(crate) fn api_version<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Vec<i64>, D::Error> {
    let v = <Vec<i64> as serde::Deserialize>::deserialize(d)?;
    if right_version(&v) {
        Ok(v)
    } else {
        Err(serde::de::Error::custom(wrong_version(&v)))
    }
}

/// An error's message without where (serde_json adds the line and column).
fn message(e: &serde_json::Error) -> String {
    let text = e.to_string();
    let at = format!(" at line {} column {}", e.line(), e.column());
    match text.strip_suffix(&at) {
        Some(m) if e.line() > 0 => m.to_owned(),
        _ => text,
    }
}

/// `path` followed by `rest`, a path inside it.
fn join(path: &mut String, rest: &str) {
    if !rest.is_empty() {
        if !path.is_empty() && !rest.starts_with('[') {
            path.push('.');
        }
        path.push_str(rest);
    }
}

/// Where in a JSON text a place is (a line and a column, as serde_json gives
/// an error's): `blocks[0].c[1]`, the value being read there, or the one
/// just read.
///
/// The text is gone through up to there, keeping only where it is. (Reading
/// the document a second time with every value's path tracked said the
/// same, and was a second copy of every type's reader: a fifth of a wasm
/// filter.)
fn path_at(json: &str, error: &serde_json::Error) -> String {
    let (line, column) = (error.line(), error.column());
    /// A list and the index of the element in it, or an object and the key
    /// of the value in it (its place in the text), once one has begun.
    enum In {
        List(Option<usize>),
        Object(Option<(usize, usize)>, bool),
    }
    let bytes = json.as_bytes();
    let line_start = json
        .match_indices('\n')
        .nth(line.saturating_sub(2))
        .map_or(0, |(i, _)| i + 1);
    let mut end = if line <= 1 {
        column
    } else {
        line_start + column
    }
    .min(bytes.len());
    let message = error.to_string();
    // An element too many is refused when seen, not read: the error is at it.
    if message.starts_with("trailing characters") {
        end = end.saturating_sub(1);
    }
    // A list or an object where another value belongs is refused before it
    // is entered: the error is at what comes before it.
    if message.starts_with("invalid type: map") || message.starts_with("invalid type: sequence") {
        let next = bytes[end..].iter().position(|b| !b.is_ascii_whitespace());
        if let Some(n) = next.filter(|n| matches!(bytes[end + n], b'[' | b'{')) {
            end += n + 1;
        }
    }
    let mut stack: Vec<In> = Vec::new();
    let mut i = 0;
    while i < end {
        let byte = bytes[i];
        // a value begins: the next element of a list
        if !byte.is_ascii_whitespace() && !matches!(byte, b',' | b']' | b'}' | b':') {
            if let Some(In::List(index @ None)) = stack.last_mut() {
                *index = Some(0);
            }
        }
        match byte {
            b'"' => {
                let start = i + 1;
                i = start;
                while i < bytes.len() && bytes[i] != b'"' {
                    i += if bytes[i] == b'\\' { 2 } else { 1 };
                }
                if let Some(In::Object(key, false)) = stack.last_mut() {
                    *key = Some((start, i.min(bytes.len())));
                }
            }
            b':' => {
                if let Some(In::Object(_, value)) = stack.last_mut() {
                    *value = true;
                }
            }
            b',' => match stack.last_mut() {
                Some(In::List(index)) => *index = index.map(|n| n + 1),
                Some(In::Object(key, value)) => (*key, *value) = (None, false),
                None => {}
            },
            b'[' => stack.push(In::List(None)),
            b'{' => stack.push(In::Object(None, false)),
            b']' | b'}' => {
                stack.pop();
            }
            _ => {}
        }
        i += 1;
    }
    // a tag that isn't one of the type's is the node's error
    if message.starts_with("unknown variant") {
        if let Some(In::Object(Some((start, close)), _)) = stack.last() {
            if &json[*start..*close] == "t" {
                stack.pop();
            }
        }
    }
    // an element too many is the list's
    if message.starts_with("trailing characters") {
        if let Some(In::List(index)) = stack.last_mut() {
            *index = None;
        }
    }
    let mut path = String::new();
    for place in &stack {
        match place {
            In::List(Some(index)) => path += &format!("[{index}]"),
            In::Object(Some((start, close)), _) => {
                if !path.is_empty() {
                    path.push('.');
                }
                path += &json[*start..*close];
            }
            In::List(None) | In::Object(None, _) => {}
        }
    }
    path
}

/// A node's contents that came before its tag (not pandoc's order: sorted
/// keys), read once the tag says what they are. The error is raised at the
/// node; where in its contents is kept for `from_str` (`INNER`).
pub(crate) fn contents<'a, T: serde::Deserialize<'a>, E: serde::de::Error>(
    c: &'a serde_json::value::RawValue,
) -> Result<T, E> {
    read(c.get()).map_err(|e| {
        // (of a document too deep, a path says little, at length)
        if !TOO_DEEP.with(std::cell::Cell::get) {
            let mut path = String::from("c");
            join(&mut path, &path_at(c.get(), &e));
            // and where in a node in them, if its contents came first too
            join(&mut path, &INNER.with(std::cell::Cell::take));
            INNER.with(|p| p.set(path));
        }
        // (the line and column are in the contents: of no use in the document)
        E::custom(message(&e))
    })
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
pub(crate) fn aeson_double<S: serde::Serializer>(x: &f64, s: S) -> Result<S::Ok, S::Error> {
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
pub(crate) struct Double(pub f64);

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

/// A document as pandoc's JSON.
pub fn to_string(doc: &Pandoc) -> String {
    let mut out = String::new();
    doc.json(&mut out);
    out
}

/// Write a document as pandoc's JSON.
pub fn to_writer<W: Write>(mut writer: W, doc: &Pandoc) -> std::io::Result<()> {
    // block by block, so that the whole text is never in memory
    let mut out = String::from("{\"pandoc-api-version\":");
    doc.api_version.json(&mut out);
    out.push_str(",\"meta\":");
    doc.meta.json(&mut out);
    out.push_str(",\"blocks\":[");
    for (i, block) in doc.blocks.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        block.json(&mut out);
        if out.len() >= 1 << 16 {
            writer.write_all(out.as_bytes())?;
            out.clear();
        }
    }
    out.push_str("]}");
    writer.write_all(out.as_bytes())
}

/// A value's JSON, as pandoc writes it, appended to a string. What
/// [`to_string`] writes with: the same text as the types' `Serialize` gives
/// serde_json, in half the time (no serializer between a node and the text).
pub(crate) trait Json {
    fn json(&self, out: &mut String);
}

// By byte: 0 for one written as it is, else how it is escaped (as
// serde_json does: `u` for \u00XX).
const ESCAPE: [u8; 256] = {
    let mut table = [0; 256];
    let mut i = 0;
    while i < 0x20 {
        table[i] = b'u';
        i += 1;
    }
    table[0x08] = b'b';
    table[0x09] = b't';
    table[0x0a] = b'n';
    table[0x0c] = b'f';
    table[0x0d] = b'r';
    table[b'"' as usize] = b'"';
    table[b'\\' as usize] = b'\\';
    table
};

impl Json for str {
    fn json(&self, out: &mut String) {
        out.push('"');
        let mut start = 0;
        for (i, &byte) in self.as_bytes().iter().enumerate() {
            let escape = ESCAPE[byte as usize];
            if escape == 0 {
                continue;
            }
            // (an ASCII byte: `i` is between characters)
            out.push_str(&self[start..i]);
            out.push('\\');
            if escape == b'u' {
                const HEX: &[u8; 16] = b"0123456789abcdef";
                out.push_str("u00");
                out.push(HEX[(byte >> 4) as usize] as char);
                out.push(HEX[(byte & 15) as usize] as char);
            } else {
                out.push(escape as char);
            }
            start = i + 1;
        }
        out.push_str(&self[start..]);
        out.push('"');
    }
}

impl Json for Text {
    #[inline]
    fn json(&self, out: &mut String) {
        self.as_str().json(out)
    }
}

impl Json for i64 {
    fn json(&self, out: &mut String) {
        use fmt::Write as _;
        let _ = write!(out, "{self}");
    }
}

impl Json for bool {
    fn json(&self, out: &mut String) {
        out.push_str(if *self { "true" } else { "false" });
    }
}

/// A `Double`, as [`aeson_double`].
pub(crate) fn write_double(x: f64, out: &mut String) {
    if x.is_nan() {
        out.push_str("null");
    } else if x.is_infinite() {
        out.push_str(if x > 0.0 { "\"+inf\"" } else { "\"-inf\"" });
    } else {
        out.push_str(&haskell_double(x));
    }
}

impl<T: Json> Json for Vec<T> {
    fn json(&self, out: &mut String) {
        out.push('[');
        for (i, x) in self.iter().enumerate() {
            if i > 0 {
                out.push(',');
            }
            x.json(out);
        }
        out.push(']');
    }
}

impl<T: Json> Json for Box<T> {
    #[inline]
    fn json(&self, out: &mut String) {
        (**self).json(out)
    }
}

impl<T: Json> Json for Option<T> {
    fn json(&self, out: &mut String) {
        match self {
            Some(x) => x.json(out),
            None => out.push_str("null"),
        }
    }
}

impl<A: Json, B: Json> Json for (A, B) {
    fn json(&self, out: &mut String) {
        out.push('[');
        self.0.json(out);
        out.push(',');
        self.1.json(out);
        out.push(']');
    }
}

impl<V: Json> Json for std::collections::BTreeMap<Text, V> {
    fn json(&self, out: &mut String) {
        out.push('{');
        for (i, (key, value)) in self.iter().enumerate() {
            if i > 0 {
                out.push(',');
            }
            key.json(out);
            out.push(':');
            value.json(out);
        }
        out.push('}');
    }
}

/// Reads `{"t": tag}` for an enum-like type, checking the tag.
pub(crate) fn de_tag<'de, D: serde::Deserializer<'de>>(
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
#[derive(PartialEq, Eq)]
pub(crate) enum Key {
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
pub(crate) struct Tag(pub &'static [&'static str]);

impl Tag {
    /// The tag, or an unknown variant error.
    pub(crate) fn check<E: serde::de::Error>(
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

    /// Its `pandoc-api-version`: always one pandoc reads as this crate's
    /// (the same major and minor version as [`PANDOC_API_VERSION`]).
    pub fn api_version(&self) -> &[i64] {
        &self.api_version
    }

    /// Walk the document with a visitor, reading it.
    pub fn visit<V: Visit + ?Sized>(&self, v: &mut V) {
        v.visit_pandoc(self);
    }

    /// Walk the document with a visitor, changing it.
    pub fn visit_mut<V: VisitMut + ?Sized>(&mut self, v: &mut V) {
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
        let mut doc = from_reader(std::io::stdin().lock()).map_err(|e| e.to_string())?;
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

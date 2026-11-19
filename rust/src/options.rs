//! A filter's options, from the document's metadata and from a code block's
//! YAML header, read by pandoc (panflute's `yaml_filter`).
//!
//! [`yaml_block`] splits a block's text into its header and its data, by
//! pandoc's rule for a YAML metadata block; pandoc reads the header (panir
//! reads no YAML); [`Options::of`] puts it over the document's metadata
//! under the filter's name; [`yaml_filter`] does it all for a document, with
//! one call of the reader for all of its blocks.

use std::error::Error;
use std::ops::Deref;

use serde_json::{Map, Value};

use crate::{Block, CodeBlock, Ctx, Filter, Meta, MetaValue, Pandoc, apply, lua_number, stringify};

/// A text's YAML header and its data, by pandoc's rule for a YAML metadata
/// block, where one starts the text: the first line is `---`, the next is
/// not blank, and a later line is `---` or `...` (each perhaps followed by
/// spaces or tabs; not indented, not four of them). The header is the lines
/// between, each with its newline (`""` for `---` then `---`); the data is
/// everything after the closing line. No header: `(None, text)`. A `---`
/// line later in the text opens nothing: text appended to the block (an
/// include) is never read as options.
///
/// ```
/// use panir::yaml_block;
///
/// assert_eq!(yaml_block("---\ncaption: Sales\n---\na,b\n"), (Some("caption: Sales\n"), "a,b\n"));
/// assert_eq!(yaml_block("---\n---"), (Some(""), ""));
/// assert_eq!(yaml_block("a,b\n---\nx: 1\n---\n"), (None, "a,b\n---\nx: 1\n---\n"));
/// ```
pub fn yaml_block(text: &str) -> (Option<&str>, &str) {
    let Some((first, _)) = text.split_once('\n') else {
        return (None, text);
    };
    if !is_line(first, &["---"]) {
        return (None, text);
    }
    let start = first.len() + 1;
    let mut at = start;
    loop {
        let (line, next) = match text[at..].find('\n') {
            Some(i) => (&text[at..at + i], Some(at + i + 1)),
            None => (&text[at..], None),
        };
        if at == start && is_line(line, &[""]) {
            return (None, text);
        }
        if is_line(line, &["---", "..."]) {
            return (Some(&text[start..at]), next.map_or("", |n| &text[n..]));
        }
        match next {
            Some(n) => at = n,
            None => return (None, text),
        }
    }
}

/// Whether `line` is one of `marks`, perhaps followed by spaces or tabs.
fn is_line(line: &str, marks: &[&str]) -> bool {
    marks.contains(&line.trim_end_matches([' ', '\t']))
}

/// Metadata as plain data, as a filter reads its options: a `MetaBool` a
/// boolean, a `MetaString` a string, `MetaInlines` and `MetaBlocks` their
/// text ([`stringify()`]), a `MetaList` an array, a `MetaMap` (or the
/// metadata, or [`Options`]) an object (panflute's
/// `get_metadata(builtin=True)`). Nothing is guessed: text stays text,
/// `"10"` too ([`lua_number`] reads a number).
///
/// ```
/// use panir::{MetaValue, to_data};
/// use serde_json::json;
///
/// let v: MetaValue = [("n", MetaValue::from("10")), ("on", true.into())].into_iter().collect();
/// assert_eq!(to_data(&v), json!({"n": "10", "on": true}));
/// assert_eq!(to_data(&MetaValue::from(panir::inlines("a  b"))), json!("a b"));
/// ```
pub fn to_data<T: ToData + ?Sized>(x: &T) -> Value {
    x.to_data()
}

/// What [`to_data`] takes: a metadata value, the metadata, [`Options`].
pub trait ToData {
    /// The value as plain data.
    fn to_data(&self) -> Value;
}

impl ToData for MetaValue {
    fn to_data(&self) -> Value {
        match self {
            MetaValue::MetaBool(b) => Value::Bool(*b),
            MetaValue::MetaString(s) => Value::String(s.to_string()),
            MetaValue::MetaInlines(_) | MetaValue::MetaBlocks(_) => Value::String(stringify(self)),
            MetaValue::MetaList(xs) => Value::Array(xs.iter().map(ToData::to_data).collect()),
            MetaValue::MetaMap(m) => m.to_data(),
        }
    }
}

impl ToData for Meta {
    fn to_data(&self) -> Value {
        Value::Object(map_data(self))
    }
}

impl ToData for Options {
    fn to_data(&self) -> Value {
        self.0.to_data()
    }
}

fn map_data(m: &Meta) -> Map<String, Value> {
    m.iter()
        .map(|(k, v)| (k.to_string(), v.to_data()))
        .collect()
}

/// A filter's options: metadata values by key, read raw (`options.get("k")`,
/// as the [`Meta`] it derefs to: a caption wants its inlines) or converted
/// on request: [`text`](Options::text), [`boolean`](Options::boolean),
/// [`number`](Options::number), [`list`](Options::list),
/// [`map`](Options::map), each `None` where the key is missing or its value
/// is of another shape.
///
/// What pandoc makes of a header (read as a metadata block, `-f markdown`;
/// checked with pandoc 3.14): `true` and `True` are booleans (`yes` is
/// text); numbers are text, as pandoc writes them (`010` is `"10"`, `1e3`
/// `"1000"`); `null` and `~` are empty text; text is read as markdown, so a
/// caption arrives as inlines, but a text of only a tab is lost, `'` is a
/// curly quote and `...` an ellipsis (`-f markdown-smart` keeps them),
/// escapes, entities and raw HTML are read; a `|` scalar is blocks.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Options(pub Meta);

impl Deref for Options {
    type Target = Meta;
    fn deref(&self) -> &Meta {
        &self.0
    }
}

impl Options {
    /// The options under `name` in the document's metadata (`meta`; ignored
    /// unless a map), each key's value replaced whole by the header's
    /// (`header`: a block's header, read as a document's metadata), as
    /// pandoc merges metadata blocks: no deeper merge.
    ///
    /// ```
    /// use panir::{Meta, MetaValue, Options};
    ///
    /// let mut meta = Meta::new();
    /// let defaults: MetaValue = [("caption", "Tables"), ("width", "0.4")].into_iter().collect();
    /// meta.insert("pantable".into(), defaults);
    /// let header = Meta::from([("caption".into(), MetaValue::from(panir::inlines("Sales")))]);
    /// let options = Options::of(&meta, "pantable", Some(&header));
    /// assert_eq!(options.get("caption"), header.get("caption"));
    /// assert_eq!((options.text("caption").as_deref(), options.number("width")), (Some("Sales"), Some(0.4)));
    /// assert_eq!(options.boolean("width"), None);
    /// ```
    pub fn of(meta: &Meta, name: &str, header: Option<&Meta>) -> Self {
        let mut options = match meta.get(name) {
            Some(MetaValue::MetaMap(m)) => m.clone(),
            _ => Meta::new(),
        };
        if let Some(header) = header {
            options.extend(header.iter().map(|(k, v)| (k.clone(), v.clone())));
        }
        Options(options)
    }

    /// Text (a `MetaString`), or the text of inlines or blocks.
    pub fn text(&self, key: &str) -> Option<String> {
        match self.get(key)? {
            MetaValue::MetaString(s) => Some(s.to_string()),
            v @ (MetaValue::MetaInlines(_) | MetaValue::MetaBlocks(_)) => Some(stringify(v)),
            _ => None,
        }
    }

    /// A `MetaBool`'s value.
    pub fn boolean(&self, key: &str) -> Option<bool> {
        match self.get(key)? {
            MetaValue::MetaBool(b) => Some(*b),
            _ => None,
        }
    }

    /// Text read as a number, as [`lua_number`] reads it.
    pub fn number(&self, key: &str) -> Option<f64> {
        lua_number(&self.text(key)?)
    }

    /// A `MetaList`'s items, as plain data ([`to_data`]).
    pub fn list(&self, key: &str) -> Option<Vec<Value>> {
        match self.get(key)? {
            MetaValue::MetaList(xs) => Some(xs.iter().map(ToData::to_data).collect()),
            _ => None,
        }
    }

    /// A `MetaMap`, as plain data ([`to_data`]).
    pub fn map(&self, key: &str) -> Option<Map<String, Value>> {
        match self.get(key)? {
            MetaValue::MetaMap(m) => Some(map_data(m)),
            _ => None,
        }
    }
}

/// Code blocks with a YAML header, panflute's `yaml_filter`: each code block
/// with one of `classes` is replaced by what `function(options, data,
/// block)` returns, as a [`Filter`]'s `block` (`Ok(None)` keeps it,
/// `Ok(Some(blocks))` is spliced in its place, `vec![]` deletes it).
/// `options` is [`Options::of`] the document's metadata, `name` and the
/// header; `data` the text after the header ([`yaml_block`]), or all of it.
///
/// `read` reads texts of markdown into documents, one each: the host's
/// (libpandoc's `read_many`), or the `pandoc` command. It is called once,
/// with every header as a document (`---`, the header, `---`); not at all
/// if no block has one. A header pandoc reads no metadata from (a YAML
/// scalar or list: blocks come back) is no header, and the whole text is
/// data, as pandoc would read it.
///
/// An error of `read` is returned as it is; one of `function` as a
/// [`FilterError`](crate::FilterError), saying where the block is.
///
/// ```no_run
/// use panir::{Block, CodeBlock, Options, Pandoc};
///
/// fn read(_texts: &[String]) -> Result<Vec<Pandoc>, String> {
///     todo!("libpandoc's read_many, or the pandoc command")
/// }
///
/// fn table(options: &Options, data: &str, _: &CodeBlock) -> Result<Option<Vec<Block>>, String> {
///     let caption = options.text("caption").unwrap_or_default();
///     Ok(Some(vec![Block::from(caption.as_str()), Block::from(data)]))
/// }
///
/// panir::filter(|doc, _| panir::yaml_filter(doc, &["table"], "pantable", read, table));
/// ```
pub fn yaml_filter<E1, E2>(
    doc: &mut Pandoc,
    classes: &[&str],
    name: &str,
    read: impl FnOnce(&[String]) -> Result<Vec<Pandoc>, E1>,
    function: impl FnMut(&Options, &str, &CodeBlock) -> Result<Option<Vec<Block>>, E2>,
) -> Result<(), Box<dyn Error + Send + Sync>>
where
    E1: Into<Box<dyn Error + Send + Sync>>,
    E2: Into<Box<dyn Error + Send + Sync>>,
{
    let mut find = Find {
        classes,
        texts: vec![],
    };
    apply(doc, &mut find, None)?;
    let splits: Vec<_> = find.texts.iter().map(|t| yaml_block(t)).collect();
    let headers: Vec<String> = (splits.iter())
        .filter_map(|(h, _)| h.map(|h| format!("---\n{h}---\n")))
        .collect();
    let read_docs = if headers.is_empty() {
        vec![]
    } else {
        read(&headers).map_err(Into::into)?
    };
    let mut read_docs = read_docs.into_iter();
    let mut todo = vec![];
    for (text, (header, data)) in find.texts.iter().zip(splits) {
        let mut meta = None;
        let mut data = data;
        if header.is_some() {
            let got = read_docs
                .next()
                .ok_or("read returned fewer documents than texts")?;
            if got.blocks.is_empty() {
                meta = Some(got.meta);
            } else {
                data = text;
            }
        }
        todo.push((Options::of(&doc.meta, name, meta.as_ref()), data.to_owned()));
    }
    let mut replace = Replace {
        classes,
        todo: todo.into_iter(),
        function,
    };
    apply(doc, &mut replace, None)?;
    Ok(())
}

/// A code block with one of `classes`.
fn wanted<'a>(x: &'a Block, classes: &[&str]) -> Option<&'a CodeBlock> {
    match x {
        Block::CodeBlock(c) if c.attr.classes.iter().any(|x| classes.contains(&x.as_str())) => {
            Some(c)
        }
        _ => None,
    }
}

/// The texts of the blocks, in the walk's order.
struct Find<'a> {
    classes: &'a [&'a str],
    texts: Vec<String>,
}

impl Filter for Find<'_> {
    fn block(&mut self, x: &mut Block, _: &mut Ctx) -> Option<Vec<Block>> {
        if let Some(c) = wanted(x, self.classes) {
            self.texts.push(c.text.to_string());
        }
        None
    }
}

/// The blocks replaced, in the same order.
struct Replace<'a, F> {
    classes: &'a [&'a str],
    todo: std::vec::IntoIter<(Options, String)>,
    function: F,
}

impl<F, E> Filter for Replace<'_, F>
where
    F: FnMut(&Options, &str, &CodeBlock) -> Result<Option<Vec<Block>>, E>,
    E: Into<Box<dyn Error + Send + Sync>>,
{
    fn block(&mut self, x: &mut Block, ctx: &mut Ctx) -> Option<Vec<Block>> {
        let c = wanted(x, self.classes)?;
        let (options, data) = self.todo.next()?;
        match (self.function)(&options, &data, c) {
            Ok(replacement) => replacement,
            Err(e) => ctx.fail(e),
        }
    }
}

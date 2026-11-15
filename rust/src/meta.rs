//! Metadata as pandoc's Lua filters read and write it: Lua's truth, a
//! lookup through maps, one value or a list of them, a value as inlines or
//! blocks, and metadata made from plain Rust values, as pandoc's Lua
//! converts plain Lua values.

use crate::{Block, Inline, Meta, MetaValue, Text};
use start::Start;

/// Whether a metadata value is true as Lua sees it (`if meta.x then`,
/// `meta.x or default`): there, and not `false`. An empty string, list or
/// map is true, as the text `false` is. It takes what `meta.get("x")` gives,
/// or a value in hand.
///
/// ```
/// use panir::{MetaValue, truthy};
///
/// let mut meta = panir::Meta::new();
/// meta.insert("draft".into(), false.into());
/// meta.insert("title".into(), "".into());
/// assert!(!truthy(meta.get("draft")));
/// assert!(!truthy(meta.get("missing")));
/// assert!(truthy(meta.get("title")));
/// assert!(meta.values().any(|v| truthy(v)));
/// ```
pub fn truthy<'a>(value: impl Into<Option<&'a MetaValue>>) -> bool {
    !matches!(value.into(), None | Some(MetaValue::MetaBool(false)))
}

/// The value if it is true as Lua sees it ([`truthy`]), else `None`: Lua's
/// `meta.x or d` is `given(meta.get("x")).unwrap_or(&d)`, and `meta.x and
/// f(meta.x)` is `given(meta.get("x")).map(f)`.
///
/// ```
/// use panir::{MetaValue, given, stringify};
///
/// let mut meta = panir::Meta::new();
/// meta.insert("draft".into(), false.into());
/// meta.insert("class".into(), "wide".into());
/// assert_eq!(given(meta.get("draft")), None);
/// assert_eq!(given(meta.get("class")).map_or("plain".into(), stringify), "wide");
/// let lang = ["quot-lang", "lang", "class"].iter().find_map(|k| given(meta.get(*k)));
/// assert_eq!(lang, Some(&MetaValue::from("wide")));
/// ```
pub fn given<'a>(value: impl Into<Option<&'a MetaValue>>) -> Option<&'a MetaValue> {
    value.into().filter(|v| truthy(*v))
}

/// The value at a path of keys, each but the last in a map (`MetaMap`):
/// Lua's `((meta.a or {}).b or {}).c`, `None` where a key is missing or a
/// value on the way is not a map (where Lua would fail, on `true`, or find a
/// method, on a string or a list). It starts from the metadata (a [`Meta`],
/// as a `MetaMap`'s content is too), a metadata value, or one perhaps
/// missing (an `Option` of a value, or of a reference to one, as
/// `meta.remove` and `meta.get` give): anything else than a map has no keys.
///
/// ```
/// use panir::{MetaValue, lookup};
///
/// let mut meta = panir::Meta::new();
/// meta.insert("a".into(), [("b", "c")].into_iter().collect());
/// assert_eq!(lookup(&meta, &["a", "b"]), Some(&MetaValue::from("c")));
/// assert_eq!(lookup(&meta, &["a", "b", "c"]), None); // (a string has no fields)
/// assert_eq!(lookup(&meta, &["x", "b"]), None);
/// // from a value, taken out or not
/// assert_eq!(lookup(&meta["a"], &["b"]), Some(&MetaValue::from("c")));
/// let a = meta.remove("a");
/// assert_eq!(lookup(&a, &["b"]), Some(&MetaValue::from("c")));
/// assert_eq!(lookup(&meta.get("x"), &["b"]), None);
/// ```
pub fn lookup<'a, T: Start + ?Sized>(from: &'a T, path: &[&str]) -> Option<&'a MetaValue> {
    let (last, path) = path.split_last()?;
    let mut map = from.as_map()?;
    for key in path {
        map = map.get(*key)?.as_map()?;
    }
    map.get(*last)
}

mod start {
    use crate::{Meta, MetaValue};

    /// What [`lookup`](super::lookup) starts from, as the map it reads
    /// first: `None` for what isn't a map.
    pub trait Start {
        fn as_map(&self) -> Option<&Meta>;
    }

    impl Start for Meta {
        fn as_map(&self) -> Option<&Meta> {
            Some(self)
        }
    }

    impl Start for MetaValue {
        fn as_map(&self) -> Option<&Meta> {
            match self {
                MetaValue::MetaMap(m) => Some(m),
                _ => None,
            }
        }
    }

    impl<T: Start + ?Sized> Start for &T {
        fn as_map(&self) -> Option<&Meta> {
            (**self).as_map()
        }
    }

    impl<T: Start> Start for Option<T> {
        fn as_map(&self) -> Option<&Meta> {
            self.as_ref()?.as_map()
        }
    }
}

/// One value or a list of them, as a list: a `MetaList`'s items, nothing for
/// a missing value, any other value alone (a filter's `ensure_list`, the
/// shapes `header-includes` takes). It takes what `meta.get("x")` gives, or
/// a value in hand.
///
/// ```
/// use panir::{MetaValue, items};
///
/// let one = MetaValue::from("a");
/// let list: MetaValue = ["a", "b"].into_iter().collect();
/// assert_eq!(items(&one).len(), 1);
/// assert_eq!(items(&list).len(), 2);
/// assert!(items(None).is_empty());
/// ```
pub fn items<'a>(value: impl Into<Option<&'a MetaValue>>) -> &'a [MetaValue] {
    match value.into() {
        None => &[],
        Some(MetaValue::MetaList(xs)) => xs,
        Some(v) => std::slice::from_ref(v),
    }
}

/// As [`items`], owning: one value or a list of them, taken out of the
/// document (`meta.remove("x")`, an `Option`, or a value), as a list of
/// them, moved: a `MetaList`'s items, nothing for a missing value, any
/// other value alone.
///
/// ```
/// use panir::{Block, MetaValue, into_items};
///
/// let mut meta = panir::Meta::new();
/// meta.insert("header-includes".into(), "<meta>".into());
/// // a raw block before what is there, whatever its shape
/// let mut header = vec![MetaValue::from(Block::raw("html", "<style/>"))];
/// header.extend(into_items(meta.remove("header-includes")));
/// meta.insert("header-includes".into(), header.into());
/// assert_eq!(panir::items(meta.get("header-includes")).len(), 2);
/// ```
pub fn into_items(value: impl Into<Option<MetaValue>>) -> Vec<MetaValue> {
    match value.into() {
        None => vec![],
        Some(MetaValue::MetaList(xs)) => xs,
        Some(v) => vec![v],
    }
}

const INLINES: &str = "inlines (MetaInlines, MetaString, MetaMap, or a MetaList of MetaStrings)";

/// A metadata value as inlines, as pandoc's Lua converts it where inlines go
/// (`pandoc.Inlines(meta.x)`, `pandoc.Span(meta.x)`): inlines as they are
/// (cloned, as Lua's are values), text its words, a map none, a list a `Str`
/// of each item's text, unsplit, if every item is text (a `MetaString`, as
/// `-M` makes); blocks, a boolean or a list of anything else fail, as in Lua:
/// an `Err` naming the kind, for [`Ctx::ok`](crate::Ctx::ok).
///
/// ```
/// use panir::{Inline, MetaValue, inlines_of};
///
/// assert_eq!(inlines_of(&"a b".into()), Ok(panir::inlines("a b")));
/// let texts: MetaValue = ["a b", "c"].into_iter().collect();
/// assert_eq!(inlines_of(&texts), Ok(vec![Inline::Str("a b".into()), Inline::Str("c".into())]));
/// assert_eq!(
///     inlines_of(&true.into()).unwrap_err(),
///     "expected inlines (MetaInlines, MetaString, MetaMap, or a MetaList of MetaStrings), got MetaBool"
/// );
/// ```
pub fn inlines_of(value: &MetaValue) -> Result<Vec<Inline>, String> {
    inlines_in(value, "", INLINES)
}

fn inlines_in(value: &MetaValue, at: &str, expected: &str) -> Result<Vec<Inline>, String> {
    match value {
        MetaValue::MetaInlines(xs) => Ok(xs.clone()),
        MetaValue::MetaString(s) => Ok(crate::inlines(s)),
        MetaValue::MetaMap(_) => Ok(vec![]),
        MetaValue::MetaList(xs) => (xs.iter().enumerate())
            .map(|(i, x)| match x {
                MetaValue::MetaString(s) => Ok(Inline::Str(s.clone())),
                _ => {
                    let at = if at.is_empty() { "MetaList" } else { at };
                    Err(format!(
                        "{at}.content[{i}]: expected an inline (a MetaString), got {}",
                        x.tag()
                    ))
                }
            })
            .collect(),
        _ if at.is_empty() => Err(format!("expected {expected}, got {}", value.tag())),
        _ => Err(format!("{at}: expected {expected}, got {}", value.tag())),
    }
}

/// A metadata value as blocks, as pandoc's Lua converts it where blocks go
/// (`pandoc.Blocks(meta.x)`, `pandoc.Div(meta.x)`): blocks as they are
/// (cloned), text a `Plain` of its words, a map none, inlines a `Plain` of
/// each inline (Lua's, as inlines are a list), a list a `Plain` of each
/// item as [`inlines_of`] makes it; a boolean, or a list holding blocks, a
/// boolean or a list of other than text, fail, as in Lua: an `Err` naming
/// the kind. So `blocks_of(&into_items(v).into())` is each of `v`'s items
/// as one block, as a Lua list of them given to `pandoc.Blocks` (or
/// `pandoc.MetaBlocks`, as filters write `header-includes`).
///
/// ```
/// use panir::{Block, Inline, MetaValue, blocks_of};
///
/// let words: MetaValue = panir::inlines("a b").into();
/// assert_eq!(blocks_of(&words).unwrap().len(), 3); // a Plain each
/// assert_eq!(blocks_of(&"a b".into()), Ok(vec![Block::Plain(panir::inlines("a b"))]));
/// let mixed: MetaValue = vec![words, MetaValue::MetaMap(Default::default())].into();
/// assert_eq!(
///     blocks_of(&mixed),
///     Ok(vec![Block::Plain(panir::inlines("a b")), Block::Plain(vec![])])
/// );
/// assert!(blocks_of(&false.into()).is_err());
/// ```
pub fn blocks_of(value: &MetaValue) -> Result<Vec<Block>, String> {
    match value {
        MetaValue::MetaBlocks(xs) => Ok(xs.clone()),
        MetaValue::MetaInlines(xs) => {
            Ok(xs.iter().map(|x| Block::Plain(vec![x.clone()])).collect())
        }
        MetaValue::MetaString(s) => Ok(crate::blocks(s)),
        MetaValue::MetaMap(_) => Ok(vec![]),
        MetaValue::MetaList(xs) => {
            let expected = format!("a block ({}", &INLINES["inlines (".len()..]);
            (xs.iter().enumerate())
                .map(|(i, x)| {
                    inlines_in(x, &format!("MetaList.content[{i}]"), &expected).map(Block::Plain)
                })
                .collect()
        }
        MetaValue::MetaBool(_) => Err(format!(
            "expected blocks (MetaBlocks, MetaInlines, MetaString, MetaMap, or a MetaList), got {}",
            value.tag()
        )),
    }
}

// Plain values as metadata, as pandoc's Lua converts them: a string is a
// MetaString, a boolean a MetaBool, inlines MetaInlines, blocks MetaBlocks,
// metadata values a MetaList, a map a MetaMap.

impl From<&str> for MetaValue {
    fn from(s: &str) -> Self {
        MetaValue::MetaString(s.into())
    }
}

impl From<String> for MetaValue {
    fn from(s: String) -> Self {
        MetaValue::MetaString(s.into())
    }
}

impl From<Text> for MetaValue {
    fn from(s: Text) -> Self {
        MetaValue::MetaString(s)
    }
}

impl From<bool> for MetaValue {
    fn from(b: bool) -> Self {
        MetaValue::MetaBool(b)
    }
}

impl From<Inline> for MetaValue {
    fn from(x: Inline) -> Self {
        MetaValue::MetaInlines(vec![x])
    }
}

impl From<Vec<Inline>> for MetaValue {
    fn from(xs: Vec<Inline>) -> Self {
        MetaValue::MetaInlines(xs)
    }
}

impl From<Block> for MetaValue {
    fn from(x: Block) -> Self {
        MetaValue::MetaBlocks(vec![x])
    }
}

impl From<Vec<Block>> for MetaValue {
    fn from(xs: Vec<Block>) -> Self {
        MetaValue::MetaBlocks(xs)
    }
}

impl From<Vec<MetaValue>> for MetaValue {
    fn from(xs: Vec<MetaValue>) -> Self {
        MetaValue::MetaList(xs)
    }
}

impl From<Meta> for MetaValue {
    fn from(m: Meta) -> Self {
        MetaValue::MetaMap(m)
    }
}

/// A `MetaList` of what converts to metadata: `["a", "b"].into_iter().collect()`.
impl<T: Into<MetaValue>> FromIterator<T> for MetaValue {
    fn from_iter<I: IntoIterator<Item = T>>(iter: I) -> Self {
        MetaValue::MetaList(iter.into_iter().map(Into::into).collect())
    }
}

/// A `MetaMap` of keys and what converts to metadata: `[("k", "v")].into_iter().collect()`.
impl<K: Into<Text>, V: Into<MetaValue>> FromIterator<(K, V)> for MetaValue {
    fn from_iter<I: IntoIterator<Item = (K, V)>>(iter: I) -> Self {
        MetaValue::MetaMap(
            iter.into_iter()
                .map(|(k, v)| (k.into(), v.into()))
                .collect(),
        )
    }
}

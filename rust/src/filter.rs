//! Filters as pandoc runs Lua filters: functions on inlines, blocks, lists of
//! them, the metadata and the document, in one of three orders.
//!
//! ```
//! use panir::{apply, Ctx, Filter, Inline};
//!
//! struct Upper;
//!
//! impl Filter for Upper {
//!     fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
//!         if let Inline::Str(s) = x {
//!             *s = s.to_uppercase().into();
//!         }
//!         None
//!     }
//! }
//!
//! let mut doc = panir::Pandoc::new(vec![panir::Block::Para(panir::inlines("a b"))]);
//! apply(&mut doc, &mut Upper, None).unwrap();
//! assert_eq!(panir::to_string(&doc).contains("\"A\""), true);
//! ```
//!
//! For other nodes (`Cell`, `Attr`, ...), and any order of one's own, use
//! [`VisitMut`].

use std::error::Error;
use std::fmt;

use crate::path::{Locate, Path, Target};
use crate::{
    Block, Inline, MAX_DEPTH, Meta, MetaValue, Pandoc, Text, VisitMut, walk_block, walk_inline,
    walk_meta_value,
};

thread_local! {
    /// How many lists deep the filter walks on this thread are: a walk started
    /// by a function (`apply` on a fragment) goes on from the one it is in.
    static DEPTH: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}

/// The depth as it was, again, when a walk ends (or a panic leaves it).
struct Restore(usize);

impl Drop for Restore {
    fn drop(&mut self) {
        DEPTH.set(self.0);
    }
}

/// The order a filter's functions are called in: Lua's `traverse`.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash)]
pub enum Order {
    /// As pandoc's Lua filters by default, and Haskell's `walk`: one walk
    /// per kind, each bottom-up: every inline, then every list of inlines,
    /// then every block, then every list of blocks; then the metadata, then
    /// the document.
    #[default]
    Typewise,
    /// As Lua's `traverse = "topdown"`, and pandocfilters: the document, the
    /// metadata, then from the root down, a list before its elements and a
    /// node before its children, which are walked in its replacement too,
    /// unless the function calls [`Ctx::skip_children`]. What a function
    /// returns is not given to a function again (nor its elements); their
    /// children are walked.
    Topdown,
    /// As panflute: one walk, each node after its children, a list after
    /// its elements, the metadata after what is in it, the document last.
    Bottomup,
}

/// The pandoc conversion a filter runs in, as panir's Python `Conversion`:
/// the output format's name (what pandoc passes a JSON filter), and, when
/// known, the input and output formats with extensions, the reader's
/// options and the conversion's options (defaults-file keys).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Conversion {
    /// The output format's name, e.g. `html5`.
    pub format: Option<String>,
    /// The formats with extensions, e.g. `commonmark_x-smart`.
    pub input_format: Option<String>,
    pub output_format: Option<String>,
    /// The reader's options (pandoc's `PANDOC_READER_OPTIONS`).
    pub reader_options: Option<serde_json::Value>,
    /// The conversion's options, in defaults-file keys.
    pub options: Option<serde_json::Value>,
}

impl Conversion {
    /// The conversion of a JSON filter pandoc runs: the output format is its
    /// first argument, the rest in the environment (`PANDOC_READER_OPTIONS`,
    /// and `PANDOC_INPUT_FORMAT` and `PANDOC_OUTPUT_FORMAT` where set, as
    /// libpandoc does; proposed to pandoc in jgm/pandoc#11016).
    pub fn from_env() -> Self {
        // (args() panics on an argument that isn't Unicode)
        let format = std::env::args_os()
            .nth(1)
            .and_then(|s| s.into_string().ok());
        Self::from_parts(format, |k| std::env::var(k).ok())
    }

    /// As [`Conversion::from_env`], from the first argument and a lookup.
    pub fn from_parts(format: Option<String>, env: impl Fn(&str) -> Option<String>) -> Self {
        let get = |k: &str| env(k).filter(|v| !v.is_empty());
        Conversion {
            format,
            input_format: get("PANDOC_INPUT_FORMAT"),
            output_format: get("PANDOC_OUTPUT_FORMAT"),
            reader_options: get("PANDOC_READER_OPTIONS")
                .and_then(|v| serde_json::from_str(&v).ok()),
            options: None,
        }
    }
}

/// What a filter function knows besides its node: the conversion.
///
/// (Not its parent, or where it is: the function has the node as `&mut`,
/// and nothing else may reach it meanwhile. A walk that needs them is a
/// [`VisitMut`] keeping its own.)
pub struct Ctx<'a> {
    conversion: &'a Conversion,
    meta: Option<&'a Meta>,
    skip: bool,
    in_list: bool,
    error: Option<Box<dyn Error + Send + Sync>>,
}

impl<'a> Ctx<'a> {
    fn new(conversion: &'a Conversion, meta: Option<&'a Meta>) -> Self {
        Ctx {
            conversion,
            meta,
            skip: false,
            in_list: false,
            error: None,
        }
    }

    /// The output format pandoc passed, if any.
    pub fn format(&self) -> Option<&'a str> {
        self.conversion.format.as_deref()
    }

    /// The conversion the filter runs in.
    pub fn conversion(&self) -> &'a Conversion {
        self.conversion
    }

    /// The document's metadata (as Lua filters read it from a global their
    /// `Meta` function set, and panir's other languages from `ctx.doc`).
    /// While a metadata value is walked, the other fields: the value is out
    /// of the map meanwhile, as it can't be lent while it changes. `None`
    /// for `meta` and `pandoc`, which have it, and in a fragment.
    pub fn meta(&self) -> Option<&'a Meta> {
        self.meta
    }

    /// Whether the node is in a list, where what the function returns is
    /// spliced (`vec![]` deletes it): an inline's and a block's always; a
    /// metadata value's in a `MetaList`, not in a map (a field of the
    /// metadata, or of a `MetaMap`), whose value is replaced by one.
    pub fn in_list(&self) -> bool {
        self.in_list
    }

    /// Don't walk the node's children, or its replacement's (Lua's `return
    /// el, false`). Top-down only: in the other orders the children came
    /// first, and this does nothing (as Lua ignores `false` there).
    pub fn skip_children(&mut self) {
        self.skip = true;
    }

    /// Fail: the walk stops when the function returns (what it returns is
    /// not used), and [`apply`] returns the error, with where it happened
    /// ([`FilterError`]). Returns `None`, for `return ctx.fail(...)`; in a
    /// function returning nothing, `ctx.fail::<()>(...)`. An error that is a
    /// `FilterError` (of a walk the function started: [`Ctx::apply`]) is
    /// returned as it is, saying where it happened in that walk.
    ///
    /// ```
    /// use panir::{Ctx, Filter, Inline};
    ///
    /// struct NoEmph;
    /// impl Filter for NoEmph {
    ///     fn inline(&mut self, x: &mut Inline, ctx: &mut Ctx) -> Option<Vec<Inline>> {
    ///         match x {
    ///             Inline::Emph(_) => ctx.fail("no emphasis, please"),
    ///             _ => None,
    ///         }
    ///     }
    /// }
    ///
    /// let mut doc = panir::Pandoc::new(vec![panir::Block::Para(vec![Inline::Emph(vec![])])]);
    /// let e = panir::apply(&mut doc, &mut NoEmph, None).unwrap_err();
    /// assert_eq!(e.path, "blocks[0].content[0]");
    /// assert_eq!(
    ///     e.to_string(),
    ///     "no emphasis, please\n  in filter function inline, on the Emph at blocks[0].content[0]"
    /// );
    /// ```
    pub fn fail<T>(&mut self, error: impl Into<Box<dyn Error + Send + Sync>>) -> Option<T> {
        self.error.get_or_insert(error.into());
        None
    }

    /// Apply a filter to a fragment from inside a function, as Lua's
    /// `el:walk{...}` there: [`apply_with`] with this walk's conversion,
    /// as deep as this walk is (no deeper than [`MAX_DEPTH`](crate::MAX_DEPTH)
    /// in all). The fragment's walk has no metadata (`ctx.meta()` is `None`
    /// in it), as any fragment's.
    ///
    /// If a function of `f` fails, this one fails with its error, as
    /// [`fail`](Ctx::fail) does, and returns `None`: `ctx.apply(&mut
    /// content, &mut Caps)?;`. The error is the inner function's, where it
    /// failed in the fragment (`inlines[2]`), as panir's other languages
    /// report an error in a fragment's walk.
    ///
    /// ```
    /// use panir::{Ctx, Filter, Inline, Order};
    ///
    /// struct Caps;
    /// impl Filter for Caps {
    ///     fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
    ///         if let Inline::Str(s) = x {
    ///             *s = s.to_uppercase().into();
    ///         }
    ///         None
    ///     }
    /// }
    ///
    /// // an emphasis becomes its content, in capitals
    /// struct Deemph;
    /// impl Filter for Deemph {
    ///     fn order(&self) -> Order {
    ///         Order::Topdown
    ///     }
    ///     fn inline(&mut self, x: &mut Inline, ctx: &mut Ctx) -> Option<Vec<Inline>> {
    ///         let Inline::Emph(content) = x else { return None };
    ///         ctx.apply(content, &mut Caps)?;
    ///         Some(std::mem::take(content))
    ///     }
    /// }
    ///
    /// let mut doc = panir::Pandoc::new(vec![panir::Block::Para(vec![
    ///     "a".into(),
    ///     Inline::Emph(panir::inlines("b c")),
    /// ])]);
    /// panir::apply(&mut doc, &mut Deemph, None).unwrap();
    /// assert_eq!(panir::stringify(&doc), "aB C");
    /// ```
    pub fn apply<T: Walkable, F: Filter + ?Sized>(&mut self, x: &mut T, f: &mut F) -> Option<()> {
        match apply_with(x, f, self.conversion) {
            Ok(()) => Some(()),
            Err(e) => self.fail(e),
        }
    }
}

/// A filter function's error ([`Ctx::fail`]), and where: the walk stopped
/// there. Displayed as panir's other languages note theirs:
///
/// ```text
/// no metadata field city
///   in filter function inline, on the Str at blocks[2].content[0]
/// ```
#[derive(Debug)]
pub struct FilterError {
    /// The node's path from the document, in pandoc's Lua field names, as
    /// panir's other languages write it (`blocks[2].content[0]`; `meta.title`;
    /// empty for the document itself); from a fragment, after its name
    /// (`Para.content[0]`, `inlines[3]`). Empty if not known (a function on an
    /// empty list).
    pub path: String,
    /// The [`Filter`] method that failed: `"inline"`, `"block"`, `"inlines"`,
    /// `"blocks"`, `"meta"` or `"pandoc"`; `None` for the walk itself, which
    /// stops at [`MAX_DEPTH`](crate::MAX_DEPTH) levels.
    pub function: Option<&'static str>,
    /// What it was called on: a constructor's name (`"Str"`), or `"Inlines"`,
    /// `"Blocks"`, `"Meta"`, `"Pandoc"`.
    pub node: &'static str,
    /// The error the function gave.
    pub error: Box<dyn Error + Send + Sync>,
}

impl fmt::Display for FilterError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self.function {
            Some(function) => write!(
                f,
                "{}\n  in filter function {function}, on the {}",
                self.error, self.node
            )?,
            None => write!(f, "{}\n  walking the {}", self.error, self.node)?,
        }
        if !self.path.is_empty() {
            write!(f, " at {}", self.path)?;
        }
        Ok(())
    }
}

impl Error for FilterError {}

/// What a walk stopped on: the error, and where, by address (the path is
/// found once the walk has stopped).
struct Failure {
    error: FilterError,
    target: Option<Target>,
    /// An error passed up as it is, its path already said.
    kept: bool,
}

impl Failure {
    /// `function` failed on `node`, at `target` (or `path`, if known). An
    /// error that is already a [`FilterError`] (of a walk the function
    /// started: [`Ctx::apply`]) is kept as it is, saying where in that walk,
    /// as panir's other languages note an error once, where it was raised.
    fn new(
        function: Option<&'static str>,
        node: &'static str,
        error: Box<dyn Error + Send + Sync>,
        target: Option<Target>,
        path: &str,
    ) -> Self {
        match error.downcast::<FilterError>() {
            Ok(error) => Failure {
                error: *error,
                target: None,
                kept: true,
            },
            Err(error) => Failure {
                error: FilterError {
                    path: path.into(),
                    function,
                    node,
                    error,
                },
                target,
                kept: false,
            },
        }
    }
}

/// An inline or a block: what a list of the filter's is of.
trait Walked: Sized + 'static {
    /// The filter's method for one, and for a list of them.
    const FUNCTION: &'static str;
    const LIST_FUNCTION: &'static str;
    /// The name of a list of them.
    const LIST: &'static str;
    fn name(&self) -> &'static str;
}

impl Walked for Inline {
    const FUNCTION: &'static str = "inline";
    const LIST_FUNCTION: &'static str = "inlines";
    const LIST: &'static str = "Inlines";
    fn name(&self) -> &'static str {
        self.tag()
    }
}

impl Walked for Block {
    const FUNCTION: &'static str = "block";
    const LIST_FUNCTION: &'static str = "blocks";
    const LIST: &'static str = "Blocks";
    fn name(&self) -> &'static str {
        self.tag()
    }
}

impl Walked for MetaValue {
    const FUNCTION: &'static str = "meta_value";
    // (a list of them has no function: a MetaList's is meta_value)
    const LIST_FUNCTION: &'static str = "meta_value";
    const LIST: &'static str = "MetaList";
    fn name(&self) -> &'static str {
        self.tag()
    }
}

/// A filter's functions. Each has a default that does nothing.
///
/// `inline` and `block` return `None` to keep the node (changed in place or
/// not), or nodes to splice in its place (`vec![]` deletes it); `inlines`,
/// `blocks`, `meta` and `pandoc` change their value in place.
///
/// `meta_value`, which pandoc's Lua doesn't have (its metadata values are
/// plain Lua values), is called on each metadata value, as panir's other
/// languages (and panflute) call theirs: in a `MetaList` it may delete or
/// splice as `inline` does; a map's value (a field of the metadata, or of a
/// `MetaMap`) is replaced by one value only (`vec![]` is an error: a key is
/// deleted from the map, by `meta`'s function or the `MetaMap`'s). In the
/// typewise order its walk is the last before `meta`'s, through the
/// metadata only.
///
/// A filter can be a trait object (`&mut dyn Filter`), and choose its
/// order when it runs:
///
/// ```
/// use panir::{Ctx, Filter, Inline, Order};
///
/// struct Upper(Order);
///
/// impl Filter for Upper {
///     fn order(&self) -> Order {
///         self.0
///     }
///     fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
///         if let Inline::Str(s) = x {
///             *s = s.to_uppercase().into();
///         }
///         None
///     }
/// }
///
/// let mut filters: Vec<Box<dyn Filter>> = vec![Box::new(Upper(Order::Topdown))];
/// let mut doc = panir::Pandoc::new(vec!["a b".into()]);
/// for f in &mut filters {
///     panir::apply(&mut doc, f.as_mut(), None).unwrap();
/// }
/// assert_eq!(doc.blocks, vec![panir::Block::from("A B")]);
/// ```
pub trait Filter {
    /// The order the functions are called in: [`Order::Typewise`], as Lua's
    /// by default, unless this says another.
    fn order(&self) -> Order {
        Order::Typewise
    }
    fn inline(&mut self, x: &mut Inline, ctx: &mut Ctx) -> Option<Vec<Inline>> {
        let _ = (x, ctx);
        None
    }
    fn block(&mut self, x: &mut Block, ctx: &mut Ctx) -> Option<Vec<Block>> {
        let _ = (x, ctx);
        None
    }
    fn inlines(&mut self, xs: &mut Vec<Inline>, ctx: &mut Ctx) {
        let _ = (xs, ctx);
    }
    fn blocks(&mut self, xs: &mut Vec<Block>, ctx: &mut Ctx) {
        let _ = (xs, ctx);
    }
    fn meta_value(&mut self, x: &mut MetaValue, ctx: &mut Ctx) -> Option<Vec<MetaValue>> {
        let _ = (x, ctx);
        None
    }
    fn meta(&mut self, meta: &mut Meta, ctx: &mut Ctx) {
        let _ = (meta, ctx);
    }
    fn pandoc(&mut self, doc: &mut Pandoc, ctx: &mut Ctx) {
        let _ = (doc, ctx);
    }
}

/// Which functions a walk calls.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Pass {
    Inline,
    Inlines,
    Block,
    Blocks,
    /// (through the metadata only)
    MetaValue,
    All,
}

struct Driver<'f, 'a, F: Filter + ?Sized> {
    f: &'f mut F,
    conversion: &'a Conversion,
    pass: Pass,
    topdown: bool,
    /// The document's metadata, for the blocks' functions.
    meta: Option<&'a Meta>,
    /// Set when a function failed: nothing more is walked.
    failure: Option<Failure>,
}

/// A filter's function on one node, and on a list of them.
type ElemFn<F, T> = fn(&mut F, &mut T, &mut Ctx) -> Option<Vec<T>>;
type ListFn<F, T> = fn(&mut F, &mut Vec<T>, &mut Ctx);

impl<'f, 'a, F: Filter + ?Sized> Driver<'f, 'a, F> {
    fn new(
        f: &'f mut F,
        conversion: &'a Conversion,
        meta: Option<&'a Meta>,
        pass: Pass,
        topdown: bool,
    ) -> Self {
        Driver {
            f,
            conversion,
            meta,
            pass,
            topdown,
            failure: None,
        }
    }

    /// Stop the walk: `function` failed on `node`.
    fn fail(
        &mut self,
        function: Option<&'static str>,
        node: &'static str,
        error: Box<dyn Error + Send + Sync>,
        target: Option<Target>,
    ) {
        self.failure = Some(Failure::new(function, node, error, target, ""));
    }

    /// The function on a list, its error noted; whether to skip its nodes.
    fn on_list<T: Walked>(&mut self, xs: &mut Vec<T>, list: ListFn<F, T>) -> bool {
        let mut ctx = Ctx::new(self.conversion, self.meta);
        list(self.f, xs, &mut ctx);
        if let Some(e) = ctx.error {
            self.fail(Some(T::LIST_FUNCTION), T::LIST, e, Target::list(xs));
        }
        ctx.skip
    }

    /// The nodes of a list, a level deeper than the list holding it (or
    /// than the walk that called the function that started this one): no
    /// deeper than [`MAX_DEPTH`].
    fn list<T: Walked>(
        &mut self,
        xs: &mut Vec<T>,
        calls: (bool, bool),
        elem: ElemFn<F, T>,
        list: ListFn<F, T>,
        children: fn(&mut Self, &mut T),
    ) {
        if self.failure.is_some() {
            return;
        }
        // (its nodes a level deeper: as reading counts, an empty list isn't)
        let depth = DEPTH.get() + 1;
        if depth > MAX_DEPTH && !xs.is_empty() {
            let e = format!(
                "nested more than {MAX_DEPTH} levels deep (a document that deep, or a top-down \
                 filter's function whose result holds what it matched, walked without end)"
            );
            self.fail(None, T::LIST, e.into(), Target::list(xs));
            return;
        }
        DEPTH.set(depth);
        self.nodes(xs, calls, elem, list, children);
        DEPTH.set(depth - 1);
    }

    /// The nodes of a list: each may be replaced, deleted or spliced; then
    /// (or first, top-down) the list.
    fn nodes<T: Walked>(
        &mut self,
        xs: &mut Vec<T>,
        (on_elem, on_list): (bool, bool),
        elem: ElemFn<F, T>,
        list: ListFn<F, T>,
        children: fn(&mut Self, &mut T),
    ) {
        let topdown = self.topdown;
        if topdown && on_list && (self.on_list(xs, list) || self.failure.is_some()) {
            return;
        }
        let mut i = 0;
        // The nodes still to walk, once one was replaced by none or several:
        // out of `xs` from then on, so that what replaces a node is only
        // appended. (Replacing in place moves all that follows, each time:
        // minutes, for every other block of 400,000 deleted.)
        let mut rest: Option<std::vec::IntoIter<T>> = None;
        // the node a function failed on
        let mut failed = None;
        loop {
            match &mut rest {
                None if i >= xs.len() => break,
                None => {}
                Some(rest) => match rest.next() {
                    Some(x) => xs.push(x), // at `i`, the end
                    None => break,
                },
            }
            if !topdown {
                children(self, &mut xs[i]);
                if self.failure.is_some() {
                    break;
                }
            }
            let mut ctx = Ctx::new(self.conversion, self.meta);
            ctx.in_list = true;
            let result = if on_elem {
                elem(self.f, &mut xs[i], &mut ctx)
            } else {
                None
            };
            if let Some(e) = ctx.error {
                // (what it returned is not used: the node stays)
                self.fail(Some(T::FUNCTION), xs[i].name(), e, None);
                failed = Some(i);
                break;
            }
            let n = match result {
                None => 1,
                Some(mut items) if items.len() == 1 => {
                    xs[i] = items.pop().expect("one item");
                    1
                }
                Some(items) => {
                    if rest.is_none() {
                        rest = Some(xs.split_off(i + 1).into_iter());
                    }
                    xs.truncate(i);
                    xs.extend(items);
                    xs.len() - i
                }
            };
            if topdown && !ctx.skip {
                for x in &mut xs[i..i + n] {
                    children(self, x);
                    if self.failure.is_some() {
                        break;
                    }
                }
            }
            if self.failure.is_some() {
                break;
            }
            i += n;
        }
        if let Some(rest) = rest {
            xs.extend(rest); // (what a failure left unwalked)
        }
        if let (Some(i), Some(failure)) = (failed, &mut self.failure)
            && !failure.kept
        {
            // (where the node is now, the list whole again)
            failure.target = Some(Target::node(&xs[i]));
        }
        if !topdown && on_list && self.failure.is_none() {
            self.on_list(xs, list);
        }
    }

    fn calls(&self, elem: Pass, list: Pass) -> (bool, bool) {
        (
            matches!(self.pass, Pass::All) || self.pass == elem,
            matches!(self.pass, Pass::All) || self.pass == list,
        )
    }
}

/// The function on a list of metadata values: none.
fn no_list<F: ?Sized, T>(_: &mut F, _: &mut Vec<T>, _: &mut Ctx) {}

impl<F: Filter + ?Sized> VisitMut for Driver<'_, '_, F> {
    fn visit_inlines(&mut self, xs: &mut Vec<Inline>) {
        let calls = self.calls(Pass::Inline, Pass::Inlines);
        self.list(xs, calls, F::inline, F::inlines, walk_inline);
    }

    fn visit_blocks(&mut self, xs: &mut Vec<Block>) {
        let calls = self.calls(Pass::Block, Pass::Blocks);
        self.list(xs, calls, F::block, F::blocks, walk_block);
    }

    /// A `MetaList`'s values: each may be replaced, deleted or spliced.
    fn visit_meta_values(&mut self, xs: &mut Vec<MetaValue>) {
        let (on_elem, _) = self.calls(Pass::MetaValue, Pass::MetaValue);
        self.list(
            xs,
            (on_elem, false),
            F::meta_value,
            no_list::<F, MetaValue>,
            walk_meta_value,
        );
    }

    /// A map's value (a field of the metadata, or of a `MetaMap`): replaced
    /// by one value, or kept.
    fn visit_meta_value(&mut self, x: &mut MetaValue) {
        if self.failure.is_some() {
            return;
        }
        let (on_elem, _) = self.calls(Pass::MetaValue, Pass::MetaValue);
        if !on_elem {
            walk_meta_value(self, x);
            return;
        }
        if !self.topdown {
            walk_meta_value(self, x);
            if self.failure.is_some() {
                return;
            }
        }
        let mut ctx = Ctx::new(self.conversion, self.meta);
        let result = self.f.meta_value(x, &mut ctx);
        let error = match (ctx.error, result) {
            (Some(e), _) => Some(e),
            (None, None) => None,
            (None, Some(mut v)) if v.len() == 1 => {
                *x = v.pop().expect("one value");
                None
            }
            (None, Some(v)) => Some(
                format!(
                    "expected one MetaValue (a map's value: to delete its key, remove it in the \
                     function of the Meta or MetaMap holding it), got {}",
                    v.len()
                )
                .into(),
            ),
        };
        if let Some(e) = error {
            self.fail(
                Some(MetaValue::FUNCTION),
                x.tag(),
                e,
                Some(Target::node(&*x)),
            );
            return;
        }
        if self.topdown && !ctx.skip {
            walk_meta_value(self, x);
        }
    }
}

/// The walks of a filter's order through `x`, with `walk`: one per kind,
/// typewise, else one; until a function fails.
fn walks<F: Filter + ?Sized, T: ?Sized>(
    x: &mut T,
    f: &mut F,
    conversion: &Conversion,
    walk: impl Fn(&mut Driver<'_, '_, F>, &mut T),
) -> Option<Failure> {
    let order = f.order();
    let passes: &[Pass] = match order {
        Order::Typewise => &[Pass::Inline, Pass::Inlines, Pass::Block, Pass::Blocks],
        Order::Topdown | Order::Bottomup => &[Pass::All],
    };
    for &pass in passes {
        let mut d = Driver::new(&mut *f, conversion, None, pass, order == Order::Topdown);
        walk(&mut d, x);
        if d.failure.is_some() {
            return d.failure;
        }
    }
    None
}

/// A failure's error, with the path of where it was in `root`, after
/// `prefix` (a fragment's name).
fn error_in<T: Locate + 'static>(
    root: &T,
    prefix: &str,
    failure: Option<Failure>,
) -> Result<(), FilterError> {
    let Some(Failure {
        mut error, target, ..
    }) = failure
    else {
        return Ok(());
    };
    if let Some(path) = target.and_then(|t| Path::find(root, t)) {
        error.path = match (prefix, path.starts_with('[')) {
            ("", _) => path,
            // (the fragment itself)
            (_, _) if path.is_empty() => prefix.into(),
            (_, true) => format!("{prefix}{path}"),
            (_, false) => format!("{prefix}.{path}"),
        };
    }
    Err(error)
}

/// A function on the metadata, or the document: its error, if it failed.
fn whole<T>(
    function: &'static str,
    node: &'static str,
    x: &mut T,
    conversion: &Conversion,
    call: impl FnOnce(&mut T, &mut Ctx),
) -> (Option<Failure>, bool) {
    let mut ctx = Ctx::new(conversion, None);
    call(x, &mut ctx);
    let path = if node == "Meta" { "meta" } else { "" };
    let failure = ctx
        .error
        .map(|error| Failure::new(Some(function), node, error, None, path));
    (failure, ctx.skip)
}

/// What a filter applies to: a document, or a fragment of one, as Lua's
/// `el:walk{...}`: a block or an inline, a list of blocks or of inlines.
///
/// On a fragment, the functions apply to what is in it, not to the node
/// itself (`block` on a block's blocks, not on it); a list's function
/// applies to the list itself too (`blocks` on a `Vec<Block>`); `meta` and
/// `pandoc` only to a document.
///
/// ```
/// use panir::{apply, Block, Ctx, Filter, Inline};
///
/// struct Loud;
/// impl Filter for Loud {
///     fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
///         match x {
///             Inline::Str(s) => Some(vec![Inline::Str(s.to_uppercase().into())]),
///             Inline::Emph(xs) => Some(std::mem::take(xs)), // not the fragment's own
///             _ => None,
///         }
///     }
/// }
///
/// let mut emph = Inline::Emph(vec!["a".into(), Inline::Emph(vec!["b".into()])]);
/// apply(&mut emph, &mut Loud, None).unwrap();
/// assert_eq!(emph, Inline::Emph(vec!["A".into(), "B".into()]));
/// ```
pub trait Walkable: sealed::Sealed {
    /// [`apply_with`] on this.
    #[doc(hidden)]
    fn apply_filter<F: Filter + ?Sized>(
        &mut self,
        f: &mut F,
        conversion: &Conversion,
    ) -> Result<(), FilterError>;
}

mod sealed {
    pub trait Sealed {}
    impl Sealed for crate::Pandoc {}
    impl Sealed for crate::Block {}
    impl Sealed for crate::Inline {}
    impl Sealed for Vec<crate::Block> {}
    impl Sealed for Vec<crate::Inline> {}
}

impl Walkable for Block {
    fn apply_filter<F: Filter + ?Sized>(
        &mut self,
        f: &mut F,
        conversion: &Conversion,
    ) -> Result<(), FilterError> {
        let failure = walks(self, f, conversion, |d, x| walk_block(d, x));
        error_in(self, self.tag(), failure)
    }
}

impl Walkable for Inline {
    fn apply_filter<F: Filter + ?Sized>(
        &mut self,
        f: &mut F,
        conversion: &Conversion,
    ) -> Result<(), FilterError> {
        let failure = walks(self, f, conversion, |d, x| walk_inline(d, x));
        error_in(self, self.tag(), failure)
    }
}

impl Walkable for Vec<Block> {
    fn apply_filter<F: Filter + ?Sized>(
        &mut self,
        f: &mut F,
        conversion: &Conversion,
    ) -> Result<(), FilterError> {
        let failure = walks(self, f, conversion, |d, xs| d.visit_blocks(xs));
        error_in(self, "blocks", failure)
    }
}

impl Walkable for Vec<Inline> {
    fn apply_filter<F: Filter + ?Sized>(
        &mut self,
        f: &mut F,
        conversion: &Conversion,
    ) -> Result<(), FilterError> {
        let failure = walks(self, f, conversion, |d, xs| d.visit_inlines(xs));
        error_in(self, "inlines", failure)
    }
}

impl Walkable for Pandoc {
    fn apply_filter<F: Filter + ?Sized>(
        &mut self,
        f: &mut F,
        conversion: &Conversion,
    ) -> Result<(), FilterError> {
        let failure = walk_document(self, f, conversion);
        error_in(self, "", failure)
    }
}

/// A filter on a document, in its order, until a function fails.
fn walk_document<F: Filter + ?Sized>(
    doc: &mut Pandoc,
    f: &mut F,
    conversion: &Conversion,
) -> Option<Failure> {
    let order = f.order();
    let passes: &[Pass] = match order {
        Order::Typewise => &[
            Pass::Inline,
            Pass::Inlines,
            Pass::Block,
            Pass::Blocks,
            Pass::MetaValue,
        ],
        Order::Topdown | Order::Bottomup => &[Pass::All],
    };
    let topdown = order == Order::Topdown;
    if topdown {
        let (failure, skip) = whole("pandoc", "Pandoc", doc, conversion, |doc, c| {
            f.pandoc(doc, c)
        });
        if failure.is_some() || skip {
            return failure;
        }
    }
    // the metadata's function: first top-down, else after what is in it
    let mut skip_meta = false;
    if topdown {
        let (failure, skip) = whole("meta", "Meta", &mut doc.meta, conversion, |m, c| {
            f.meta(m, c)
        });
        if failure.is_some() {
            return failure;
        }
        skip_meta = skip;
    }
    for &pass in passes {
        // the metadata, each value lent the others (out of the map while
        // it is walked: it can't be lent while it changes), then the
        // blocks, lent the metadata
        let Pandoc { meta, blocks, .. } = &mut *doc;
        if !skip_meta {
            let keys: Vec<Text> = meta.keys().cloned().collect();
            for k in keys {
                let Some(mut v) = meta.remove(&k) else {
                    continue;
                };
                let mut d = Driver::new(&mut *f, conversion, Some(&*meta), pass, topdown);
                d.visit_meta_value(&mut v);
                let failure = d.failure;
                // (a failure on the value itself: where it is in the map now)
                let itself = failure
                    .as_ref()
                    .is_some_and(|e| e.target == Some(Target::node(&v)));
                let v: &MetaValue = meta.entry(k).or_insert(v);
                if let Some(mut failure) = failure {
                    if itself {
                        failure.target = Some(Target::node(v));
                    }
                    return Some(failure);
                }
            }
        }
        if pass == Pass::MetaValue {
            continue; // (no metadata value in the blocks)
        }
        if order == Order::Bottomup {
            let (failure, _) = whole("meta", "Meta", meta, conversion, |m, c| f.meta(m, c));
            if failure.is_some() {
                return failure;
            }
        }
        let mut d = Driver::new(&mut *f, conversion, Some(&*meta), pass, topdown);
        d.visit_blocks(blocks);
        if d.failure.is_some() {
            return d.failure;
        }
    }
    if topdown {
        return None;
    }
    if order == Order::Typewise {
        let (failure, _) = whole("meta", "Meta", &mut doc.meta, conversion, |m, c| {
            f.meta(m, c)
        });
        if failure.is_some() {
            return failure;
        }
    }
    whole("pandoc", "Pandoc", doc, conversion, |doc, c| {
        f.pandoc(doc, c)
    })
    .0
}

/// Apply a filter to a document, as pandoc applies a Lua filter, in the
/// filter's [`Order`], or to a fragment of one ([`Walkable`]). `format` is
/// the output format ([`Ctx::format`]).
///
/// A function that fails ([`Ctx::fail`]) stops the walk: the error is
/// returned, with where it happened; the document is as the functions
/// before left it.
pub fn apply<T: Walkable, F: Filter + ?Sized>(
    x: &mut T,
    f: &mut F,
    format: Option<&str>,
) -> Result<(), FilterError> {
    let conversion = Conversion {
        format: format.map(str::to_owned),
        ..Conversion::default()
    };
    apply_with(x, f, &conversion)
}

/// As [`apply`], knowing the whole conversion ([`Ctx::conversion`]).
pub fn apply_with<T: Walkable, F: Filter + ?Sized>(
    x: &mut T,
    f: &mut F,
    conversion: &Conversion,
) -> Result<(), FilterError> {
    let _restore = Restore(DEPTH.get());
    x.apply_filter(f, conversion)
}

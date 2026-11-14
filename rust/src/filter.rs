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
//! apply(&mut doc, &mut Upper, None);
//! assert_eq!(panir::to_string(&doc).contains("\"A\""), true);
//! ```
//!
//! For other nodes (`Cell`, `Attr`, ...), and any order of one's own, use
//! [`VisitMut`].

use crate::{walk_block, walk_inline, Block, Inline, Meta, Pandoc, VisitMut};

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
    /// unless the function calls [`Ctx::skip_children`].
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
    skip: bool,
}

impl<'a> Ctx<'a> {
    fn new(conversion: &'a Conversion) -> Self {
        Ctx {
            conversion,
            skip: false,
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

    /// Don't walk the node's children, or its replacement's (Lua's `return
    /// el, false`). Top-down only: in the other orders the children came
    /// first, and this does nothing (as Lua ignores `false` there).
    pub fn skip_children(&mut self) {
        self.skip = true;
    }
}

/// A filter's functions. Each has a default that does nothing.
///
/// `inline` and `block` return `None` to keep the node (changed in place or
/// not), or nodes to splice in its place (`vec![]` deletes it); `inlines`,
/// `blocks`, `meta` and `pandoc` change their value in place.
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
///     panir::apply(&mut doc, f.as_mut(), None);
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
    All,
}

struct Driver<'f, 'a, F: Filter + ?Sized> {
    f: &'f mut F,
    conversion: &'a Conversion,
    pass: Pass,
    topdown: bool,
}

/// A filter's function on one node, and on a list of them.
type ElemFn<F, T> = fn(&mut F, &mut T, &mut Ctx) -> Option<Vec<T>>;
type ListFn<F, T> = fn(&mut F, &mut Vec<T>, &mut Ctx);

impl<F: Filter + ?Sized> Driver<'_, '_, F> {
    /// The nodes of a list: each may be replaced, deleted or spliced; then
    /// (or first, top-down) the list.
    fn list<T>(
        &mut self,
        xs: &mut Vec<T>,
        (on_elem, on_list): (bool, bool),
        elem: ElemFn<F, T>,
        list: ListFn<F, T>,
        children: fn(&mut Self, &mut T),
    ) {
        let topdown = self.topdown;
        if topdown && on_list {
            let mut ctx = Ctx::new(self.conversion);
            list(self.f, xs, &mut ctx);
            if ctx.skip {
                return;
            }
        }
        let mut i = 0;
        // The nodes still to walk, once one was replaced by none or several:
        // out of `xs` from then on, so that what replaces a node is only
        // appended. (Replacing in place moves all that follows, each time:
        // minutes, for every other block of 400,000 deleted.)
        let mut rest: Option<std::vec::IntoIter<T>> = None;
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
            }
            let mut ctx = Ctx::new(self.conversion);
            let result = if on_elem {
                elem(self.f, &mut xs[i], &mut ctx)
            } else {
                None
            };
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
                }
            }
            i += n;
        }
        if !topdown && on_list {
            list(self.f, xs, &mut Ctx::new(self.conversion));
        }
    }

    fn calls(&self, elem: Pass, list: Pass) -> (bool, bool) {
        (
            matches!(self.pass, Pass::All) || self.pass == elem,
            matches!(self.pass, Pass::All) || self.pass == list,
        )
    }
}

impl<F: Filter + ?Sized> VisitMut for Driver<'_, '_, F> {
    fn visit_inlines(&mut self, xs: &mut Vec<Inline>) {
        let calls = self.calls(Pass::Inline, Pass::Inlines);
        self.list(xs, calls, F::inline, F::inlines, walk_inline);
    }

    fn visit_blocks(&mut self, xs: &mut Vec<Block>) {
        let calls = self.calls(Pass::Block, Pass::Blocks);
        self.list(xs, calls, F::block, F::blocks, walk_block);
    }
}

/// The walks of a filter's order through `x`, with `walk`: one per kind,
/// typewise, else one.
fn walks<F: Filter + ?Sized, T: ?Sized>(
    x: &mut T,
    f: &mut F,
    conversion: &Conversion,
    walk: impl Fn(&mut Driver<'_, '_, F>, &mut T),
) {
    let order = f.order();
    let passes: &[Pass] = match order {
        Order::Typewise => &[Pass::Inline, Pass::Inlines, Pass::Block, Pass::Blocks],
        Order::Topdown | Order::Bottomup => &[Pass::All],
    };
    for &pass in passes {
        let mut d = Driver {
            f: &mut *f,
            conversion,
            pass,
            topdown: order == Order::Topdown,
        };
        walk(&mut d, x);
    }
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
/// apply(&mut emph, &mut Loud, None);
/// assert_eq!(emph, Inline::Emph(vec!["A".into(), "B".into()]));
/// ```
pub trait Walkable: sealed::Sealed {
    /// [`apply_with`] on this.
    #[doc(hidden)]
    fn apply_filter<F: Filter + ?Sized>(&mut self, f: &mut F, conversion: &Conversion);
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
    fn apply_filter<F: Filter + ?Sized>(&mut self, f: &mut F, conversion: &Conversion) {
        walks(self, f, conversion, |d, x| walk_block(d, x));
    }
}

impl Walkable for Inline {
    fn apply_filter<F: Filter + ?Sized>(&mut self, f: &mut F, conversion: &Conversion) {
        walks(self, f, conversion, |d, x| walk_inline(d, x));
    }
}

impl Walkable for Vec<Block> {
    fn apply_filter<F: Filter + ?Sized>(&mut self, f: &mut F, conversion: &Conversion) {
        walks(self, f, conversion, |d, xs| d.visit_blocks(xs));
    }
}

impl Walkable for Vec<Inline> {
    fn apply_filter<F: Filter + ?Sized>(&mut self, f: &mut F, conversion: &Conversion) {
        walks(self, f, conversion, |d, xs| d.visit_inlines(xs));
    }
}

impl Walkable for Pandoc {
    fn apply_filter<F: Filter + ?Sized>(&mut self, f: &mut F, conversion: &Conversion) {
        let doc = self;
        let ctx = || Ctx::new(conversion);
        match f.order() {
            Order::Topdown => {
                let mut c = ctx();
                f.pandoc(doc, &mut c);
                if c.skip {
                    return;
                }
                let mut c = ctx();
                f.meta(&mut doc.meta, &mut c);
                let mut d = Driver {
                    f,
                    conversion,
                    pass: Pass::All,
                    topdown: true,
                };
                if !c.skip {
                    for v in doc.meta.values_mut() {
                        d.visit_meta_value(v);
                    }
                }
                d.visit_blocks(&mut doc.blocks);
            }
            Order::Bottomup => {
                let mut d = Driver {
                    f: &mut *f,
                    conversion,
                    pass: Pass::All,
                    topdown: false,
                };
                for v in doc.meta.values_mut() {
                    d.visit_meta_value(v);
                }
                d.f.meta(&mut doc.meta, &mut ctx());
                d.visit_blocks(&mut doc.blocks);
                f.pandoc(doc, &mut ctx());
            }
            Order::Typewise => {
                walks(doc, f, conversion, |d, doc| d.visit_pandoc(doc));
                f.meta(&mut doc.meta, &mut ctx());
                f.pandoc(doc, &mut ctx());
            }
        }
    }
}

/// Apply a filter to a document, as pandoc applies a Lua filter, in the
/// filter's [`Order`], or to a fragment of one ([`Walkable`]). `format` is
/// the output format ([`Ctx::format`]).
pub fn apply<T: Walkable, F: Filter + ?Sized>(x: &mut T, f: &mut F, format: Option<&str>) {
    let conversion = Conversion {
        format: format.map(str::to_owned),
        ..Conversion::default()
    };
    apply_with(x, f, &conversion);
}

/// As [`apply`], knowing the whole conversion ([`Ctx::conversion`]).
pub fn apply_with<T: Walkable, F: Filter + ?Sized>(x: &mut T, f: &mut F, conversion: &Conversion) {
    x.apply_filter(f, conversion);
}

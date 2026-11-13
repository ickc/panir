//! Filters as pandoc runs Lua filters: functions on inlines, blocks, lists of
//! them, the metadata and the document, in one of three orders.
//!
//! ```
//! use panir::{apply, Ctx, Filter, Inline, Typewise};
//!
//! struct Upper;
//!
//! impl Filter for Upper {
//!     type Order = Typewise;
//!     fn inline(&mut self, x: &mut Inline, _: &mut Ctx<Typewise>) -> Option<Vec<Inline>> {
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
use std::marker::PhantomData;

/// The order a filter's functions are called in.
pub trait Order: sealed::Sealed {
    #[doc(hidden)]
    const KIND: Kind;
}

#[doc(hidden)]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Kind {
    Typewise,
    Topdown,
    Bottomup,
}

mod sealed {
    pub trait Sealed {}
    impl Sealed for super::Typewise {}
    impl Sealed for super::Topdown {}
    impl Sealed for super::Bottomup {}
}

/// As pandoc's Lua filters by default, and Haskell's `walk`: one walk per
/// kind, each bottom-up: every inline, then every list of inlines, then every
/// block, then every list of blocks; then the metadata, then the document.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Typewise;

/// As Lua's `traverse = "topdown"`, and pandocfilters: the document, the
/// metadata, then from the root down, a list before its elements and a node
/// before its children, which are walked in its replacement too, unless the
/// function calls [`Ctx::skip_children`].
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Topdown;

/// As panflute: one walk, each node after its children, a list after its
/// elements, the metadata after what is in it, the document last.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Bottomup;

impl Order for Typewise {
    const KIND: Kind = Kind::Typewise;
}
impl Order for Topdown {
    const KIND: Kind = Kind::Topdown;
}
impl Order for Bottomup {
    const KIND: Kind = Kind::Bottomup;
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

/// What a filter function knows besides its node.
pub struct Ctx<'a, O: Order> {
    conversion: &'a Conversion,
    skip: bool,
    order: PhantomData<O>,
}

impl<'a, O: Order> Ctx<'a, O> {
    fn new(conversion: &'a Conversion) -> Self {
        Ctx {
            conversion,
            skip: false,
            order: PhantomData,
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
}

impl Ctx<'_, Topdown> {
    /// Don't walk the node's children, or its replacement's (Lua's `return
    /// el, false`). Only top-down: otherwise the children came first, so
    /// other orders' contexts don't have it:
    ///
    /// ```compile_fail
    /// fn f(ctx: &mut panir::Ctx<panir::Typewise>) {
    ///     ctx.skip_children();
    /// }
    /// ```
    pub fn skip_children(&mut self) {
        self.skip = true;
    }
}

/// A filter's functions. Each has a default that does nothing.
///
/// `inline` and `block` return `None` to keep the node (changed in place or
/// not), or nodes to splice in its place (`vec![]` deletes it); `inlines`,
/// `blocks`, `meta` and `pandoc` change their value in place.
pub trait Filter {
    type Order: Order;

    fn inline(&mut self, x: &mut Inline, ctx: &mut Ctx<Self::Order>) -> Option<Vec<Inline>> {
        let _ = (x, ctx);
        None
    }
    fn block(&mut self, x: &mut Block, ctx: &mut Ctx<Self::Order>) -> Option<Vec<Block>> {
        let _ = (x, ctx);
        None
    }
    fn inlines(&mut self, xs: &mut Vec<Inline>, ctx: &mut Ctx<Self::Order>) {
        let _ = (xs, ctx);
    }
    fn blocks(&mut self, xs: &mut Vec<Block>, ctx: &mut Ctx<Self::Order>) {
        let _ = (xs, ctx);
    }
    fn meta(&mut self, meta: &mut Meta, ctx: &mut Ctx<Self::Order>) {
        let _ = (meta, ctx);
    }
    fn pandoc(&mut self, doc: &mut Pandoc, ctx: &mut Ctx<Self::Order>) {
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

struct Driver<'f, 'a, F: Filter> {
    f: &'f mut F,
    conversion: &'a Conversion,
    pass: Pass,
}

/// A filter's function on one node, and on a list of them.
type ElemFn<F, T> = fn(&mut F, &mut T, &mut Ctx<<F as Filter>::Order>) -> Option<Vec<T>>;
type ListFn<F, T> = fn(&mut F, &mut Vec<T>, &mut Ctx<<F as Filter>::Order>);

impl<F: Filter> Driver<'_, '_, F> {
    fn topdown() -> bool {
        F::Order::KIND == Kind::Topdown
    }

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
        let topdown = Self::topdown();
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

impl<F: Filter> VisitMut for Driver<'_, '_, F> {
    fn visit_inlines(&mut self, xs: &mut Vec<Inline>) {
        let calls = self.calls(Pass::Inline, Pass::Inlines);
        self.list(xs, calls, F::inline, F::inlines, walk_inline);
    }

    fn visit_blocks(&mut self, xs: &mut Vec<Block>) {
        let calls = self.calls(Pass::Block, Pass::Blocks);
        self.list(xs, calls, F::block, F::blocks, walk_block);
    }
}

/// Apply a filter to a document, as pandoc applies a Lua filter, in the
/// filter's [`Order`]. `format` is the output format ([`Ctx::format`]).
pub fn apply<F: Filter>(doc: &mut Pandoc, f: &mut F, format: Option<&str>) {
    let conversion = Conversion {
        format: format.map(str::to_owned),
        ..Conversion::default()
    };
    apply_with(doc, f, &conversion);
}

/// As [`apply`], knowing the whole conversion ([`Ctx::conversion`]).
pub fn apply_with<F: Filter>(doc: &mut Pandoc, f: &mut F, conversion: &Conversion) {
    let ctx = || Ctx::<F::Order>::new(conversion);
    match F::Order::KIND {
        Kind::Topdown => {
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
            };
            if !c.skip {
                for v in doc.meta.values_mut() {
                    d.visit_meta_value(v);
                }
            }
            d.visit_blocks(&mut doc.blocks);
        }
        Kind::Bottomup => {
            let mut d = Driver {
                f: &mut *f,
                conversion,
                pass: Pass::All,
            };
            for v in doc.meta.values_mut() {
                d.visit_meta_value(v);
            }
            d.f.meta(&mut doc.meta, &mut ctx());
            d.visit_blocks(&mut doc.blocks);
            f.pandoc(doc, &mut ctx());
        }
        Kind::Typewise => {
            for pass in [Pass::Inline, Pass::Inlines, Pass::Block, Pass::Blocks] {
                Driver {
                    f: &mut *f,
                    conversion,
                    pass,
                }
                .visit_pandoc(doc);
            }
            f.meta(&mut doc.meta, &mut ctx());
            f.pandoc(doc, &mut ctx());
        }
    }
}

//! A filter on a fragment, as Lua's `el:walk{...}`: the calls are pandoc
//! 3.14's (its Lua run on the same fragments, in each of its orders).

use panir::{apply, Block, Ctx, Filter, Inline, Meta, Order, Pandoc};

/// Notes each call, as the Lua scenarios do.
struct Noting(Order, Vec<String>);

impl Filter for Noting {
    fn order(&self) -> Order {
        self.0
    }
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
        self.1.push(match x {
            Inline::Str(s) => format!("Str:{s}"),
            Inline::Emph(_) => "Emph".into(),
            Inline::Space => "Space".into(),
            other => format!("{other:?}"),
        });
        None
    }
    fn block(&mut self, x: &mut Block, _: &mut Ctx) -> Option<Vec<Block>> {
        let name = match x {
            Block::Para(_) => "Para",
            Block::Plain(_) => "Plain",
            Block::BlockQuote(_) => "BlockQuote",
            Block::Div(_) => "Div",
            _ => "other",
        };
        self.1.push(name.into());
        None
    }
    fn inlines(&mut self, xs: &mut Vec<Inline>, _: &mut Ctx) {
        self.1.push(format!("Inlines{}", xs.len()));
    }
    fn blocks(&mut self, xs: &mut Vec<Block>, _: &mut Ctx) {
        self.1.push(format!("Blocks{}", xs.len()));
    }
    fn meta(&mut self, _: &mut Meta, _: &mut Ctx) {
        self.1.push("Meta".into());
    }
    fn pandoc(&mut self, _: &mut Pandoc, _: &mut Ctx) {
        self.1.push("Pandoc".into());
    }
}

fn div() -> Block {
    panir::Div {
        attr: Default::default(),
        content: vec![
            Block::Para(vec!["a".into(), Inline::Emph(vec!["b".into()])]),
            Block::BlockQuote(vec![Block::Plain(vec!["c".into()])]),
        ],
    }
    .into()
}

fn calls<T: panir::Walkable>(mut x: T, order: Order) -> String {
    let mut f = Noting(order, vec![]);
    apply(&mut x, &mut f, None);
    f.1.join(" ")
}

#[test]
fn which_functions_see_a_fragment() {
    let blocks = match div() {
        Block::Div(d) => d.content,
        _ => unreachable!(),
    };
    let emph = Inline::Emph(vec!["b".into(), Inline::Space]);
    let inlines = vec![Inline::from("a"), Inline::Emph(vec!["b".into()])];
    // not a node's own function (no Div, no Emph), but a list's (Blocks2,
    // Inlines2), and never Meta or Pandoc
    let typewise =
        "Str:a Str:b Emph Str:c Inlines1 Inlines2 Inlines1 Para Plain BlockQuote Blocks1 Blocks2";
    assert_eq!(calls(div(), Order::Typewise), typewise);
    assert_eq!(calls(blocks.clone(), Order::Typewise), typewise);
    assert_eq!(calls(emph.clone(), Order::Typewise), "Str:b Space Inlines2");
    assert_eq!(
        calls(inlines.clone(), Order::Typewise),
        "Str:a Str:b Emph Inlines1 Inlines2"
    );
    let topdown =
        "Blocks2 Para Inlines2 Str:a Emph Inlines1 Str:b BlockQuote Blocks1 Plain Inlines1 Str:c";
    assert_eq!(calls(div(), Order::Topdown), topdown);
    assert_eq!(calls(blocks.clone(), Order::Topdown), topdown);
    assert_eq!(calls(emph.clone(), Order::Topdown), "Inlines2 Str:b Space");
    assert_eq!(
        calls(inlines, Order::Topdown),
        "Inlines2 Str:a Emph Inlines1 Str:b"
    );
    // (bottom-up has no Lua: each node after its children)
    assert_eq!(
        calls(div(), Order::Bottomup),
        "Str:a Str:b Inlines1 Emph Inlines2 Para Str:c Inlines1 Plain Blocks1 BlockQuote Blocks2"
    );
}

#[test]
fn a_fragment_is_changed_in_place() {
    struct Loud;
    impl Filter for Loud {
        fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
            match x {
                Inline::Str(s) => Some(vec![s.to_uppercase().into()]),
                Inline::Emph(xs) => Some(std::mem::take(xs)),
                _ => None,
            }
        }
        fn block(&mut self, x: &mut Block, _: &mut Ctx) -> Option<Vec<Block>> {
            matches!(x, Block::BlockQuote(_)).then(Vec::new)
        }
    }
    let mut d = div();
    apply(&mut d, &mut Loud, None);
    let want: Block = panir::Div {
        attr: Default::default(),
        content: vec![Block::Para(vec!["A".into(), "B".into()])],
    }
    .into();
    assert_eq!(d, want);
    // as a trait object too
    let mut d = div();
    let f: &mut dyn Filter = &mut Loud;
    apply(&mut d, f, None);
    assert_eq!(d, want);
}

#[test]
fn skipping_children_is_for_top_down_only() {
    /// Asks to skip every node's children.
    struct Skips(Order, usize);
    impl Filter for Skips {
        fn order(&self) -> Order {
            self.0
        }
        fn inline(&mut self, _: &mut Inline, ctx: &mut Ctx) -> Option<Vec<Inline>> {
            self.1 += 1;
            ctx.skip_children();
            None
        }
        fn blocks(&mut self, _: &mut Vec<Block>, ctx: &mut Ctx) {
            ctx.skip_children();
        }
    }
    let n = |order| {
        let mut f = Skips(order, 0);
        apply(&mut div(), &mut f, None);
        f.1
    };
    // the Div's blocks are skipped top-down; elsewhere the children came
    // first, and it does nothing (as Lua ignores `return el, false` there)
    assert_eq!(n(Order::Topdown), 0);
    assert_eq!(n(Order::Typewise), 4);
    assert_eq!(n(Order::Bottomup), 4);
}

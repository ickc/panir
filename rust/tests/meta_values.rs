//! A filter's function for metadata values (`Filter::meta_value`), which
//! pandoc's Lua doesn't have: called as panir's other languages call
//! theirs (the shared scenario `metavalues` checks the typewise and
//! top-down orders against them).

use panir::{Block, Ctx, Filter, Inline, Meta, MetaValue, Order, Pandoc, apply};

/// `title: a`, `l: [x, drop]`, `m: {k: true}`, and a paragraph `b`.
fn doc() -> Pandoc {
    let mut doc = Pandoc::new(vec![Block::Para(vec!["b".into()])]);
    let inl = |s: &str| MetaValue::MetaInlines(vec![s.into()]);
    doc.meta.insert("title".into(), inl("a"));
    doc.meta
        .insert("l".into(), MetaValue::MetaList(vec![inl("x"), inl("drop")]));
    let mut m = Meta::new();
    m.insert("k".into(), MetaValue::MetaBool(true));
    doc.meta.insert("m".into(), MetaValue::MetaMap(m));
    doc
}

/// Notes each call (`MetaBool:true`); deletes `drop` in a list; fails on
/// the value noted as `fail`, or returns `[]` for it with `empty`.
struct Notes {
    order: Order,
    seen: Vec<String>,
    fail: &'static str,
    empty: bool,
}

impl Notes {
    fn new(order: Order) -> Self {
        Notes {
            order,
            seen: vec![],
            fail: "",
            empty: false,
        }
    }
}

impl Filter for Notes {
    fn order(&self) -> Order {
        self.order
    }
    fn meta_value(&mut self, x: &mut MetaValue, ctx: &mut Ctx) -> Option<Vec<MetaValue>> {
        let text = panir::stringify(&*x);
        let note = format!("{}:{text}", x.tag());
        self.seen.push(note.clone());
        match note.as_str() {
            "MetaInlines:drop" => Some(vec![]),
            t if t == self.fail && self.empty => Some(vec![]),
            t if t == self.fail => ctx.fail("no"),
            _ => None,
        }
    }
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
        if let Inline::Str(s) = x {
            self.seen.push(format!("Str:{s}"));
        }
        None
    }
    fn meta(&mut self, _: &mut Meta, _: &mut Ctx) {
        self.seen.push("Meta".into());
    }
}

fn calls(order: Order) -> String {
    let mut f = Notes::new(order);
    let mut d = doc();
    apply(&mut d, &mut f, None).unwrap();
    assert_eq!(
        d.meta["l"],
        MetaValue::MetaList(vec![MetaValue::MetaInlines(vec!["x".into()])])
    );
    f.seen.join(" ")
}

#[test]
fn in_each_order() {
    // typewise: after every inline, before the metadata's function; the
    // fields in key order
    assert_eq!(
        calls(Order::Typewise),
        "Str:x Str:drop Str:a Str:b MetaInlines:x MetaInlines:drop MetaList:x \
         MetaBool:true MetaMap:true MetaInlines:a Meta"
    );
    assert_eq!(
        calls(Order::Topdown),
        "Meta MetaList:xdrop MetaInlines:x Str:x MetaInlines:drop MetaMap:true \
         MetaBool:true MetaInlines:a Str:a Str:b"
    );
    assert_eq!(
        calls(Order::Bottomup),
        "Str:x MetaInlines:x Str:drop MetaInlines:drop MetaList:x MetaBool:true \
         MetaMap:true Str:a MetaInlines:a Meta Str:b"
    );
}

#[test]
fn a_failure_says_where_in_the_metadata() {
    for order in [Order::Typewise, Order::Topdown, Order::Bottomup] {
        for (text, node, path) in [
            ("MetaInlines:a", "MetaInlines", "meta.title"),
            ("MetaInlines:x", "MetaInlines", "meta.l.content[0]"),
            ("MetaBool:true", "MetaBool", "meta.m.content.k"),
        ] {
            let mut f = Notes::new(order);
            f.fail = text;
            let e = apply(&mut doc(), &mut f, None).unwrap_err();
            assert_eq!(
                (e.function, e.node, e.path.as_str()),
                (Some("meta_value"), node, path),
                "{order:?}"
            );
            assert_eq!(
                e.to_string(),
                format!("no\n  in filter function meta_value, on the {node} at {path}")
            );
        }
    }
}

#[test]
fn a_maps_value_is_replaced_by_one() {
    for order in [Order::Typewise, Order::Topdown, Order::Bottomup] {
        for (text, node, path) in [
            ("MetaInlines:a", "MetaInlines", "meta.title"),
            ("MetaBool:true", "MetaBool", "meta.m.content.k"),
        ] {
            let mut f = Notes::new(order);
            (f.fail, f.empty) = (text, true);
            let mut d = doc();
            let e = apply(&mut d, &mut f, None).unwrap_err();
            assert_eq!(
                (e.function, e.node, e.path.as_str()),
                (Some("meta_value"), node, path)
            );
            assert_eq!(
                e.error.to_string(),
                "expected one MetaValue (a map's value: to delete its key, remove it in the \
                 function of the Meta or MetaMap holding it), got 0"
            );
            // the value stays
            assert_eq!(d.meta.len(), 3);
        }
    }
}

#[test]
fn a_walk_that_does_not_call_it_is_unchanged() {
    // the default does nothing: the metadata as it was
    struct Nothing;
    impl Filter for Nothing {}
    let mut d = doc();
    apply(&mut d, &mut Nothing, None).unwrap();
    assert_eq!(d, doc());
}

#[test]
fn a_function_knows_whether_its_value_is_in_a_list() {
    // (to delete a value where it can: in a list)
    struct DeleteInLists(Order, Vec<bool>);
    impl Filter for DeleteInLists {
        fn order(&self) -> Order {
            self.0
        }
        fn meta_value(&mut self, _: &mut MetaValue, ctx: &mut Ctx) -> Option<Vec<MetaValue>> {
            self.1.push(ctx.in_list());
            ctx.in_list().then(Vec::new)
        }
        fn inline(&mut self, _: &mut Inline, ctx: &mut Ctx) -> Option<Vec<Inline>> {
            assert!(ctx.in_list());
            None
        }
    }
    for order in [Order::Typewise, Order::Topdown, Order::Bottomup] {
        let mut f = DeleteInLists(order, vec![]);
        let mut d = doc();
        apply(&mut d, &mut f, None).unwrap();
        assert_eq!(d.meta["l"], MetaValue::MetaList(vec![]));
        assert_eq!(d.meta.len(), 3);
        // l's two items in a list, the fields and m's k not
        assert_eq!(f.1.iter().filter(|&&b| b).count(), 2, "{order:?}");
        assert_eq!(f.1.len(), 6);
    }
}

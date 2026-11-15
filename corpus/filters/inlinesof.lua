-- A metadata value as inlines and as blocks, as pandoc's Lua converts it
-- where they go (`pandoc.Inlines(v)` as `pandoc.Span(v)`, `pandoc.Blocks(v)`
-- as `pandoc.Div(v)`): for each field, a paragraph of its inlines and a div
-- of its blocks, or the word "fails" where Lua fails. Text is its words, a
-- map nothing, inlines as blocks a Plain each, a list's items each one
-- inline (text: `null`'s) or one block (inlines, text, a map, a list).
-- stateless: the same with traverse = "bottomup"
local keys = {
  "text", "blocks", "on", "map", "emptymap", "null", "empty", "list",
  "nulls", "mixed", "lists", "listblocks", "listbool", "emptylist",
}
return {{
  Pandoc = function(doc)
    for _, k in ipairs(keys) do
      local para = pandoc.Inlines{pandoc.Str(k .. ":"), pandoc.Space()}
      local ok, xs = pcall(pandoc.Inlines, doc.meta[k])
      para:extend(ok and xs or {pandoc.Str("fails")})
      doc.blocks:insert(pandoc.Para(para))
      local ok2, bs = pcall(pandoc.Blocks, doc.meta[k])
      doc.blocks:insert(pandoc.Div(ok2 and bs or {pandoc.Para("fails")}, {id = k}))
    end
    return doc
  end,
}}

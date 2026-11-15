-- A Lua file returning a list of filters: each a whole walk, in turn, as
-- pandoc-quotes reads its configuration, then rewrites the quotations.
-- The first reads the metadata; the second, on every Str (the metadata's
-- too), uses what it read: in one typewise filter the inlines would come
-- before the metadata.
-- stateless: the same with traverse = "bottomup"
local mark = "?"
return {
  {Meta = function(m) mark = pandoc.utils.stringify(m.mark or "!") end},
  {Str = function(s) return pandoc.Str(s.text .. mark) end},
}

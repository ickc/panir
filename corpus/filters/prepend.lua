-- One value or a list of them, taken out of the metadata and written back
-- after a raw block, as filters write `header-includes`: a missing field,
-- one value, a list, a map, `false` (a list of a block alone is blocks, as
-- Lua converts it). And a raw inline in place of each word `raw`: one, made
-- once, returned each time (a value: each a copy).
-- stateless: the same with traverse = "bottomup"
local function items(v)
  if v == nil then return {} end
  if pandoc.utils.type(v) == "List" then return v end
  return {v}
end
local mark = pandoc.RawInline("tex", "\\raw{}")
return {{
  Meta = function(meta)
    for _, k in ipairs{"one", "list", "map", "off", "missing"} do
      local out = {pandoc.RawBlock("html", "<" .. k .. ">")}
      for _, x in ipairs(items(meta[k])) do out[#out + 1] = x end
      meta[k] = out
    end
    return meta
  end,
  Str = function(s)
    if s.text == "raw" then return mark end
  end,
}}

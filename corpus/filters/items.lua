-- One value or a list of them (a filter's `ensure_list`, the shapes
-- `header-includes` takes): a list's items, nothing for a missing value,
-- any other value alone. A paragraph says how many each field has, and
-- their text.
-- stateless: the same with traverse = "bottomup"
local function items(v)
  if v == nil then return {} end
  if pandoc.utils.type(v) == "List" then return v end
  return {v}
end
local keys = {"one", "list", "empty", "map", "nested", "off", "inlines", "missing"}
return {{
  Pandoc = function(doc)
    local out = {}
    for _, k in ipairs(keys) do
      local texts = {}
      for _, x in ipairs(items(doc.meta[k])) do texts[#texts + 1] = pandoc.utils.stringify(x) end
      out[#out + 1] = k .. "=" .. #texts .. ":" .. table.concat(texts, ",")
    end
    doc.blocks:insert(pandoc.Para(table.concat(out, " ")))
    return doc
  end,
}}

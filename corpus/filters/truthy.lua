-- Lua's truth of metadata values (`if meta.x then`, `meta.x or d`): false
-- for a missing field and for `false`, true for anything else, an empty
-- string, list or map and the text "no" included. A paragraph says each,
-- then the text of `meta.x or "d"` for each.
-- stateless: the same with traverse = "bottomup"
local keys = {"yes", "no", "text", "empty", "null", "noword", "zero", "list", "map", "block", "missing"}
return {{
  Pandoc = function(doc)
    local out = {}
    for _, k in ipairs(keys) do
      out[#out + 1] = k .. "=" .. (doc.meta[k] and "true" or "false")
    end
    local ors = {}
    for _, k in ipairs(keys) do
      ors[#ors + 1] = pandoc.utils.stringify(doc.meta[k] or "d")
    end
    out[#out + 1] = "or=" .. table.concat(ors, ",")
    doc.blocks:insert(pandoc.Para(table.concat(out, " ")))
    return doc
  end,
}}

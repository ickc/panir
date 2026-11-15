-- Lua's `pairs` over metadata values: a map's entries, a list's, inlines'
-- and blocks' items by index from 1; text, a boolean and a missing field
-- fail. A paragraph for each field: its keys (sorted: Lua's order over a
-- map changes from run to run) with their values' text, or "fails".
-- stateless: the same with traverse = "bottomup"
local keys = {"map", "list", "inlines", "blocks", "emptymap", "emptylist", "on", "null", "missing"}
return {{
  Pandoc = function(doc)
    for _, k in ipairs(keys) do
      local ok, out = pcall(function()
        local v, found, entries = doc.meta[k], {}, {}
        for key in pairs(v) do found[#found + 1] = key end
        table.sort(found)
        for _, key in ipairs(found) do
          entries[#entries + 1] = key .. "=" .. pandoc.utils.stringify(v[key])
        end
        return table.concat(entries, ",")
      end)
      doc.blocks:insert(pandoc.Para(k .. ": " .. (ok and out or "fails")))
    end
    return doc
  end,
}}

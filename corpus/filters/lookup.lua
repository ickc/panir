-- A nested option, Lua's `((meta.a or {}).b or {}).c`: the value at a path
-- of keys through maps, nothing where a key is missing or a value on the
-- way is not a map (where Lua itself would fail, on `true`, or find a
-- method, on a string or a list). A paragraph says what each path finds.
-- stateless: the same with traverse = "bottomup"
local function lookup(meta, key, ...)
  local v = meta[key]
  for _, k in ipairs{...} do
    if pandoc.utils.type(v) ~= "table" then return nil end
    v = v[k]
  end
  return v
end
local paths = {
  {"toc"}, {"toc", "depth"}, {"toc", "style", "name"}, {"toc", "missing"}, {"missing", "depth"},
  {"flag", "depth"}, {"list", "a"}, {"text", "map"}, {"toc", "depth", "x"}, {"toc", "style", "name", "x"},
}
return {{
  Pandoc = function(doc)
    local out = {}
    for _, path in ipairs(paths) do
      local v = lookup(doc.meta, table.unpack(path))
      out[#out + 1] = table.concat(path, ".") .. "=" .. (v == nil and "none" or pandoc.utils.stringify(v))
    end
    doc.blocks:insert(pandoc.Para(table.concat(out, " ")))
    return doc
  end,
}}

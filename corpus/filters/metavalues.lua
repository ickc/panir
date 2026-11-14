-- A function for metadata values, as panir's languages (and panflute)
-- have: pandoc's Lua calls none (its metadata values are plain Lua
-- values), so here a Meta function walks the metadata itself and calls
-- `value` on each, where panir would: typewise, after every Inline and
-- Block function, a value after what is in it; top-down, before the body,
-- a value before what is in it (and as pandoc's Lua walks the metadata
-- after its Meta function anyway, its Str function passes over the
-- metadata's Strs, counted). In a list, a value may be deleted or
-- spliced; in a map, replaced by one. Inline functions see what is in the
-- metadata as they see the body. Each call is noted; a last filter adds a
-- paragraph listing them.
local seen = {}
local function note(s) seen[#seen + 1] = s end

-- the function for metadata values: nil keeps the value, else a list of
-- what replaces it (a MetaString is a Lua string, a MetaBool a boolean)
local function value(v)
  local ty = pandoc.utils.type(v)
  if ty == "Inlines" then
    local text = pandoc.utils.stringify(v)
    note("MetaInlines:" .. text)
    if text == "drop" then return {} end
    if text == "two" then return {"two-a", "two-b"} end
  elseif ty == "Blocks" then note("MetaBlocks")
  elseif ty == "List" then note("MetaList:" .. #v)
  elseif ty == "table" then note("MetaMap")
  elseif ty == "string" then note("MetaString:" .. v)
  elseif ty == "boolean" then
    note("MetaBool")
    return {tostring(v)}
  end
end

local function sorted_keys(map)
  local ks = {}
  for k in pairs(map) do ks[#ks + 1] = k end
  table.sort(ks)
  return ks
end

local walk

-- what is in `v`; top-down, the Strs of its inlines are noted here too
local function children(v, topdown)
  local ty = pandoc.utils.type(v)
  if ty == "List" then
    local out = {}
    for _, x in ipairs(v) do
      local r = walk(x, topdown)
      for _, y in ipairs(r or {x}) do out[#out + 1] = y end
    end
    for i = #v, 1, -1 do v[i] = nil end
    for i, y in ipairs(out) do v[i] = y end
  elseif ty == "table" or ty == "Meta" then
    for _, k in ipairs(sorted_keys(v)) do
      local r = walk(v[k], topdown)
      if r then
        assert(#r == 1, "a map's value is replaced by one")
        v[k] = r[1]
      end
    end
  elseif topdown and (ty == "Inlines" or ty == "Blocks") then
    v:walk{Str = function(s) note("Str:" .. s.text) end}
  end
end

-- `v` and what is in it: nil, or what replaces it
function walk(v, topdown)
  if not topdown then
    children(v, false)
    return value(v)
  end
  local r = value(v)
  for _, x in ipairs(r or {v}) do children(x, true) end
  return r
end

-- how many Strs are in the metadata
local function count(m)
  local n = 0
  local function each(v)
    local ty = pandoc.utils.type(v)
    if ty == "Inlines" or ty == "Blocks" then
      v:walk{Str = function() n = n + 1 end}
    elseif ty == "List" or ty == "table" or ty == "Meta" then
      for _, x in pairs(v) do each(x) end
    end
  end
  each(m)
  return n
end

local pass = 0  -- Strs to pass over
local function str(s)
  if pass > 0 then
    pass = pass - 1
  else
    note("Str:" .. s.text)
  end
end

return {
  {
    Str = str,
    Meta = function(m)
      children(m, false)
      return m
    end,
  },
  {
    traverse = "topdown",
    Str = str,
    Meta = function(m)
      children(m, true)
      pass = count(m)
      return m
    end,
  },
  {
    Pandoc = function(doc)
      doc.blocks:insert(pandoc.Para(table.concat(seen, " ")))
      return doc
    end,
  },
}

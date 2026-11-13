-- The oracle of the filter fuzzer: a filter spec (fuzz/README.md), run by
-- pandoc's own Lua filter machinery. Each panir language interprets the same
-- spec with its own filters, and must make the same document.
--
--   PANIR_FUZZ_SPEC=spec.json pandoc -f json -t json -L fuzz/filter.lua

local fh = assert(io.open(os.getenv("PANIR_FUZZ_SPEC")))
local spec = pandoc.json.decode(fh:read("a"), false)
fh:close()

local function set(names)
  local s = {}
  for _, name in ipairs(names) do s[name] = true end
  return s
end

local TEXT = set{"Str", "Code", "Math", "RawInline", "CodeBlock", "RawBlock"}
local UNWRAP = set{
  "Emph", "Underline", "Strong", "Strikeout", "Superscript", "Subscript",
  "SmallCaps", "Quoted", "Cite", "Link", "Image", "Span", -- inlines in an Inline
  "BlockQuote", "Div", "Figure", -- blocks in a Block
}

-- every function call, in order, across the spec's filters
local n, log = 0, {}

local function called(rule, entry)
  n = n + 1
  log[#log + 1] = entry
  return not rule.mod or n % rule.mod == rule.eq
end

local function element(rule)
  local act = rule["do"]
  return function(el)
    local me = n + 1
    if not called(rule, TEXT[el.t] and (el.t .. ":" .. el.text) or el.t) then return nil end
    local inline = pandoc.utils.type(el) == "Inline"
    local function wrapped()
      if inline then return pandoc.Emph{el} end
      return pandoc.BlockQuote{el}
    end
    local function mark()
      if TEXT[el.t] then el.text = el.text .. "#" .. me end
      return el
    end
    if act == "keep" then return nil
    elseif act == "delete" then return {}
    elseif act == "dup" then return {el, el}
    elseif act == "replace" then
      if inline then return pandoc.Str("R") end
      return pandoc.Para{pandoc.Str("R")}
    elseif act == "wrap" then return wrapped()
    elseif act == "unwrap" then
      -- (pandoc's Lua calls an Image's inlines its caption)
      if UNWRAP[el.t] then return el.t == "Image" and el.caption or el.content end
      return nil
    elseif act == "mark" or act == "markret" then return mark()
    elseif act == "skip" then return nil, false
    elseif act == "wrapskip" then return wrapped(), false
    elseif act == "markskip" then return mark(), false
    end
    error("unknown action " .. tostring(act))
  end
end

local function list(rule, name)
  local act = rule["do"]
  local new = name == "Inlines" and pandoc.Inlines or pandoc.Blocks
  return function(xs)
    if not called(rule, name .. #xs) then return nil end
    if act == "keep" then return nil
    elseif act == "reverse" then
      local r = new{}
      for i = #xs, 1, -1 do r:insert(xs[i]) end
      return r
    elseif act == "dropfirst" then
      if #xs > 0 then xs:remove(1) end
      return xs
    elseif act == "append" then
      if name == "Inlines" then xs:insert(pandoc.Str("A")) else xs:insert(pandoc.Para{pandoc.Str("A")}) end
      return xs
    elseif act == "empty" then return new{}
    elseif act == "skip" then return nil, false
    end
    error("unknown action " .. tostring(act))
  end
end

local function meta(rule)
  local act = rule["do"]
  return function(m)
    if not called(rule, "Meta") then return nil end
    if act == "keep" then return nil
    elseif act == "add" then m.fuzz = pandoc.MetaBool(true); return m
    elseif act == "empty" then return pandoc.Meta{}
    end
    error("unknown action " .. tostring(act))
  end
end

local function document(rule)
  local act = rule["do"]
  return function(doc)
    if not called(rule, "Pandoc") then return nil end
    if act == "keep" then return nil
    elseif act == "prepend" then doc.blocks:insert(1, pandoc.Para{pandoc.Str("P")}); return doc
    elseif act == "clear" then doc.blocks = pandoc.Blocks{}; return doc
    end
    error("unknown action " .. tostring(act))
  end
end

local filters = {}
for _, f in ipairs(spec.filters) do
  local filter = {traverse = f.traverse}
  for name, rule in pairs(f.rules) do
    if name == "Inlines" or name == "Blocks" then filter[name] = list(rule, name)
    elseif name == "Meta" then filter[name] = meta(rule)
    elseif name == "Pandoc" then filter[name] = document(rule)
    else filter[name] = element(rule)
    end
  end
  filters[#filters + 1] = filter
end
if spec.log then
  filters[#filters + 1] = {
    Pandoc = function(doc)
      doc.blocks:insert(pandoc.Para{pandoc.Str(table.concat(log, " "))})
      return doc
    end,
  }
end
return filters

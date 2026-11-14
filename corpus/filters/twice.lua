-- A node returned twice is two nodes: each code span is doubled, and a
-- later function (the paragraph's) changes the first code span in its
-- paragraph only.
-- stateless: the same with traverse = "bottomup"
return {{
  Code = function(c) return {c, c} end,
  Para = function(p)
    for _, x in ipairs(p.content) do
      if x.t == "Code" then
        x.text = "changed"
        return p
      end
    end
  end,
}}

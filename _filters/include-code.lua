--[[
include-code.lua: show real script code in an article, never a pasted copy.

Usage in a .qmd file (the code block is left empty and filled at render time):

    ```{.python include="scripts/analyze.py" snippet="verify"}
    ```

- `include` is a path relative to the .qmd file being rendered.
- `snippet` (optional) selects the lines between two marker comments in that file:
      # --- snippet: verify ---
      ...code...
      # --- end snippet: verify ---
  Without `snippet`, the whole file is shown.

Because the article reads the very file the pipeline runs, the code a reader
sees cannot drift from the code that produced the numbers. A missing file or
snippet stops the render instead of publishing an empty block.
]]

local function read_file(path)
  local handle = io.open(path, "r")
  if not handle then
    error("include-code: cannot open " .. path)
  end
  local text = handle:read("a")
  handle:close()
  return text
end

-- Pattern-escape a snippet name so names like "load-data" match literally.
local function escape(text)
  return (text:gsub("[%^%$%(%)%%%.%[%]%*%+%-%?]", "%%%0"))
end

local function extract_snippet(text, name, path)
  local start_marker = "^%s*# %-%-%- snippet: " .. escape(name) .. " %-%-%-%s*$"
  local end_marker = "^%s*# %-%-%- end snippet: " .. escape(name) .. " %-%-%-%s*$"
  local lines, inside, found = {}, false, false
  for line in (text .. "\n"):gmatch("(.-)\n") do
    if line:match(start_marker) then
      inside, found = true, true
    elseif line:match(end_marker) then
      inside = false
    elseif inside then
      table.insert(lines, line)
    end
  end
  if not found then
    error("include-code: snippet '" .. name .. "' not found in " .. path)
  end
  return table.concat(lines, "\n")
end

function CodeBlock(block)
  local include = block.attributes["include"]
  if not include then
    return nil
  end

  local base = pandoc.path.directory(quarto.doc.input_file)
  local path = pandoc.path.join({ base, include })
  local text = read_file(path)

  local snippet = block.attributes["snippet"]
  if snippet then
    text = extract_snippet(text, snippet, include)
  else
    -- Hide snippet markers when showing a whole file; they are only signposts.
    local kept = {}
    for line in (text .. "\n"):gmatch("(.-)\n") do
      if not line:match("^%s*# %-%-%- .*snippet: .* %-%-%-%s*$") then
        table.insert(kept, line)
      end
    end
    text = table.concat(kept, "\n")
  end

  block.text = text:gsub("%s+$", "")
  block.attributes["include"] = nil
  block.attributes["snippet"] = nil
  -- Label the block with its source so readers know exactly where it lives.
  if not block.attributes["filename"] then
    block.attributes["filename"] = include
  end
  return block
end

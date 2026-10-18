import { DRAWING_TOOL_ICONS, registeredDrawingTools } from 'openalgo-charts/draw'
import { renderToStaticMarkup } from 'react-dom/server'
import { ensureHostDrawingTools, HOST_DRAW_TOOL_METADATA } from './buySellImbalance'
import { DRAW_GROUPS, drawToolIcon } from './drawTools'

beforeAll(async () => {
  // The Buy/Sell Imbalance study is registered host-side at runtime, not by
  // the chart package itself — the catalogue lists it, so the comparison
  // below needs that registration pulled in first.
  await ensureHostDrawingTools()
})

describe('trading drawing catalogue', () => {
  const entries = DRAW_GROUPS.flatMap((group) => group.sections.flatMap((section) => section.tools))

  it('exposes each installed drawing tool exactly once', () => {
    expect(entries.map((tool) => tool.id).sort()).toEqual(
      registeredDrawingTools()
        .map((tool) => tool.id)
        .sort()
    )
  })

  it('uses the installed names and glyph paths for every menu entry', () => {
    for (const tool of registeredDrawingTools()) {
      const entry = entries.find((item) => item.id === tool.id)
      expect(entry, tool.id).toBeDefined()
      expect(entry?.label, tool.id).toBe(tool.name)
      const container = document.createElement('div')
      container.innerHTML = renderToStaticMarkup(drawToolIcon(entry!.iconKey))
      // Engine tools carry the package's glyph; host-registered tools (the
      // Buy/Sell Imbalance study) carry their own metadata path instead.
      const expectedPath = DRAWING_TOOL_ICONS[tool.id] ?? HOST_DRAW_TOOL_METADATA[tool.id]?.path
      expect(container.querySelector('path')?.getAttribute('d'), tool.id).toBe(expectedPath)
    }
  })

  it('gives every glyph its own size, so a bare button cannot leave it at the SVG default', () => {
    const container = document.createElement('div')
    container.innerHTML = renderToStaticMarkup(drawToolIcon('cursor'))
    const svg = container.querySelector('svg')
    expect(svg?.getAttribute('width')).toBe('20')
    expect(svg?.getAttribute('height')).toBe('20')
    container.innerHTML = renderToStaticMarkup(drawToolIcon('cursor', 16))
    expect(container.querySelector('svg')?.getAttribute('width')).toBe('16')
  })
})

export const meta = {
  name: 'portfolio-consolidate',
  description: 'One editor per theme sees all surviving frames together: dedupe across shoots and years, one standard, best-first order',
  phases: [{ title: 'Consolidate', detail: 'one editor per theme' }],
}
const { themes, allThemes, round = 1, note = '' } = args
const THUMB = '/Volumes/G DRIVE/LR-RESCUE.noindex/thumbs/<id div 1000, zero-padded to 5 digits>/<id>.jpg'
phase('Consolidate')
const res = await parallel(themes.map(t => async () => {
  const r = await agent(`You are the editor responsible for the "${t.theme}" section of a photographer's final portfolio (20-year archive).
The JSON at ${t.index} lists contact sheets of EVERY frame that survived a strict first pass in this theme (sorted by first-pass strength), plus first-pass notes per id. View ALL sheets with the Read tool before deciding; open frames larger at ${THUMB} when needed. View the calibration sheet /Volumes/G DRIVE/LR-RESCUE.noindex/sheets/portfolio_calibration.jpg first: it shows the archive's very best frames.
${note ? note + '\n' : ''}Now that you can see the whole theme at once:
1. Remove near-duplicates and redundant frames across different shoots and years (same idea, same pose, same view): keep the strongest one.
2. Apply ONE consistent standard: every frame you keep must hold its own in a published portfolio section for this theme. Depth varies by theme; a thin theme may keep only a handful, a deep one more.
3. Order the keepers best first (the order will be used as-is).
${round === 1 ? `4. If a frame is strong enough for the portfolio but simply belongs in a DIFFERENT section, do not judge it against this theme: put it in "cut" with "rehome" set to the best-fitting section name from this list: ${JSON.stringify(allThemes)}. Use rehome only for genuinely strong frames; weak or duplicate frames get rehome = null.` : 'Some frames were moved here from other sections; judge them by exactly the same standard and dedupe them against the rest.'}
Return "portfolio": the ordered ids you keep, and "cut": the ids you remove with a short reason each${round === 1 ? ' (and rehome)' : ''}. Every id must appear exactly once in one of the two lists.`,
    { label: `r${round}:${t.theme.slice(0, 24)}`, phase: 'Consolidate', schema: { type: 'object', properties: {
      portfolio: { type: 'array', items: { type: 'integer' } },
      cut: { type: 'array', items: { type: 'object', properties: { image_id: { type: 'integer' }, reason: { type: 'string' },
        rehome: { type: ['string', 'null'] } }, required: ['image_id', 'reason', 'rehome'] } },
      section_note: { type: 'string', description: 'one or two sentences on what makes this section strong' } },
      required: ['portfolio', 'cut', 'section_note'] } })
  return r ? { theme: t.theme, portfolio: r.portfolio, cut: r.cut, section_note: r.section_note } : null
}))
const ok = res.filter(Boolean)
const failed = themes.filter((_, i) => !res[i]).map(t => t.theme)
if (failed.length) log(`WARNING: themes without result: ${failed.join(', ')}`)
return { themes: ok, failed }

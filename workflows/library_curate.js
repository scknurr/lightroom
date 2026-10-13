export const meta = {
  name: 'library-curate',
  description: 'Per-year curators build series; a library curator builds cross-year series, portfolio order and Instagram queue',
  phases: [
    { title: 'Year curators', detail: 'one curator per year reads that year\'s condensed keepers' },
    { title: 'Library curator', detail: 'merge into library-wide series and a posting queue' },
  ],
}
const { years, portfolioFile } = args
const SERIES = { type: 'object', properties: {
  series: { type: 'array', items: { type: 'object', properties: {
    name: { type: 'string' }, description: { type: 'string' }, members: { type: 'array', items: { type: 'integer' } },
    cover_id: { type: 'integer' }, best_for: { type: 'array', items: { type: 'string' } } },
    required: ['name', 'description', 'members', 'cover_id', 'best_for'] } },
  notes: { type: 'array', items: { type: 'string' } } }, required: ['series', 'notes'] }

phase('Year curators')
const perYear = await parallel(years.map(y => () => agent(`You are the curator for one year (${y.name}) of a photographer's archive.
Read the JSON list of keepers at ${y.file} (records: id, v=verdict portfolio|backstock, s=editor score, theme, title, kw=keywords, people, minors, uses, day).
Build "series": coherent chunks worth presenting together (an event, place, recurring subject, or visual style). Merge editor themes that mean the same thing. Each series: name (<= 5 words, include enough context to be unique across years, e.g. "No Kings Madison 2025"), one-sentence description, members (4-120 ids), cover_id (strongest member), best_for (subset of instagram, stock, print, web). Skip one-offs. Also 2-4 short notes on this year's strongest material.`,
  { label: `curate:${y.name}`, phase: 'Year curators', schema: SERIES })))

const yearSeries = perYear.filter(Boolean).flatMap((r, i) => r.series.map(s => ({ ...s, year: years[i].name })))
log(`${yearSeries.length} year-level series`)

phase('Library curator')
const lib = await agent(`You are the head curator of a photographer's whole archive (2000-2026). Below are the year-level series (name, year, description, size, best_for). The library's portfolio frames (id, score, title, theme, year, people, minors, uses) are at ${portfolioFile} - read it.
Produce:
1. collections: library-wide groupings that span years (e.g. "Protests", "Dogs", "Winter", "Portraits", "Corporate Events", "Travel", "Live Music") - each with name, description, and the list of year-series names it includes (by exact name). Every year-series should belong to at least one collection; it may belong to several.
2. instagram_queue: up to 150 portfolio image ids, sequenced as a varied, engaging feed (alternate subjects/colors/eras; never two from the same series back-to-back); exclude minors and anything not marked for instagram.
3. notes: 5-8 bullets on the archive's strongest bodies of work and what to develop, print, license, or post first.

YEAR SERIES (JSON): ${JSON.stringify(yearSeries.map(s => ({ name: s.name, year: s.year, description: s.description, size: s.members.length, best_for: s.best_for })))}`,
  { label: 'library-curator', phase: 'Library curator', schema: { type: 'object', properties: {
    collections: { type: 'array', items: { type: 'object', properties: { name: { type: 'string' }, description: { type: 'string' }, series: { type: 'array', items: { type: 'string' } } }, required: ['name', 'description', 'series'] } },
    instagram_queue: { type: 'array', items: { type: 'integer' } },
    notes: { type: 'array', items: { type: 'string' } } }, required: ['collections', 'instagram_queue', 'notes'] } })

return { series: yearSeries, year_notes: perYear.filter(Boolean).map((r, i) => ({ year: years[i].name, notes: r.notes })), ...(lib || {}) }

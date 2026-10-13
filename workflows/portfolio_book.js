export const meta = {
  name: 'portfolio-book',
  description: 'Three judges pick the flagship Book from every theme\'s best; votes combined; a sequencer orders it like a book',
  phases: [
    { title: 'Judges', detail: 'impact, craft, story each pick and rank 110' },
    { title: 'Sequence', detail: 'order the combined selection as a book' },
  ],
}
const { index, size = 110 } = args
const THUMB = '/Volumes/G DRIVE/LR-RESCUE.noindex/thumbs/<id div 1000, zero-padded to 5 digits>/<id>.jpg'
const LENSES = [
  ['impact', 'IMPACT: frames that stop someone cold and anchor a wall or a cover.'],
  ['craft', 'CRAFT: light, composition, timing and finish; the most accomplished photographs.'],
  ['story', 'STORY: emotion, decisive moments, history and place; documentary and human moments weigh heavily.'],
]
phase('Judges')
const votes = (await parallel(LENSES.map(([k, lens]) => async () => {
  const r = await agent(`You are one of three judges choosing the flagship BOOK of a photographer's 20-year archive: about ${size} photographs that represent the very best of the work and its range (dogs, bike racing, protests and civic life, portraits, weddings, Madison, travel, landscapes and more).
The JSON at ${index} lists contact sheets of each theme's top portfolio frames ("theme_of" maps id -> theme). View EVERY sheet with the Read tool before choosing; open frames larger at ${THUMB} when needed.
Your lens: ${lens}
Pick exactly ${size} ids, ranked best first. Keep range: no theme should dominate unless it truly is the strongest work, and no near-duplicates.`,
    { label: `book:${k}`, phase: 'Judges', schema: { type: 'object', properties: { picks: { type: 'array', items: { type: 'integer' } } }, required: ['picks'] } })
  return r ? { lens: k, picks: r.picks } : null
}))).filter(Boolean)

const points = new Map()
for (const v of votes) [...new Set(v.picks)].forEach((id, r) => points.set(id, (points.get(id) || 0) + (size - r)))
const agreed = [...points.entries()].sort((a, b) => b[1] - a[1]).slice(0, size).map(([id]) => id)
log(`${votes.length} judges; ${points.size} distinct picks; top ${agreed.length} combined`)

phase('Sequence')
const seq = await agent(`You are sequencing a photographer's flagship photo BOOK. These ${agreed.length} photographs were chosen by three judges (listed strongest first): ${JSON.stringify(agreed)}.
Open each at ${THUMB} (or use the contact sheets in ${index}). Order them as a book is sequenced: a strong opening image, deliberate pacing and pairings (spreads), movement across themes, places and years rather than blocks, and a memorable closing image. Return the full ordered list (every id exactly once) and a short note on the opening, the arc and the close.`,
  { label: 'sequencer', phase: 'Sequence', schema: { type: 'object', properties: {
    order: { type: 'array', items: { type: 'integer' } }, note: { type: 'string' } }, required: ['order', 'note'] } })
return { votes, agreed, order: seq ? seq.order : agreed, note: seq ? seq.note : 'sequencer failed; judge order used' }

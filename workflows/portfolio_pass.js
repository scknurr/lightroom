export const meta = {
  name: 'portfolio-pass',
  description: 'Strict, calibrated portfolio re-judgment per theme chunk, with an advocate for owner-starred demotions',
  phases: [
    { title: 'Judge', detail: 'one strict editor per time-ordered theme chunk, anchored on a calibration sheet' },
    { title: 'Advocate', detail: 'second opinion on every owner-starred frame the judge demoted' },
  ],
}
const { n, calibration } = args
const chunks = Array.from({ length: n }, (_, c) => ({ c, index: `/Volumes/G DRIVE/LR-RESCUE.noindex/portfolio/chunk_${String(c).padStart(3, '0')}.json` }))
const THUMB = '/Volumes/G DRIVE/LR-RESCUE.noindex/thumbs/<id div 1000, zero-padded to 5 digits>/<id>.jpg'
const JUDGE = { type: 'object', properties: { frames: { type: 'array', items: { type: 'object', properties: {
  image_id: { type: 'integer' }, keep: { type: 'boolean' }, strength: { type: 'integer', minimum: 1, maximum: 10 },
  duplicate_of: { type: ['integer', 'null'] }, reason: { type: 'string' } },
  required: ['image_id', 'keep', 'strength', 'duplicate_of', 'reason'] } } }, required: ['frames'] }
const ADVOCATE = { type: 'object', properties: { verdicts: { type: 'array', items: { type: 'object', properties: {
  image_id: { type: 'integer' }, reinstate: { type: 'boolean' }, reason: { type: 'string' } },
  required: ['image_id', 'reinstate', 'reason'] } } }, required: ['verdicts'] }

const results = await pipeline(
  chunks,
  (c) => agent(`You are a strict senior photo editor finalizing a photographer's PORTFOLIO (20-year archive). The theme is given in the "theme" field of ${c.index}.
FIRST view the calibration sheet ${calibration}: nine of the archive's very best frames as chosen by a judging panel. They define the top of the scale (10).
THEN read ${c.index} and view each of its contact sheets (3x3 grids, tiles labeled "<slot>  #<image_id>"); open a frame larger when needed at ${THUMB}. Frames are in capture-time order.
For EVERY frame decide:
- keep (portfolio) only if it would sit comfortably in a curated portfolio next to the calibration frames, OR it is clearly the definitive frame of a meaningful subject, moment or story in this theme. Judge by genre (documentary/protest: moment and story; portraits: expression; landscapes: light and composition; pets: character; events: only genuinely exceptional frames). Corporate and routine event coverage almost never qualifies.
- duplicate_of: if a frame is a near-duplicate or a weaker variant of another frame in this chunk (same moment, pose or scene), set duplicate_of to the stronger one and keep=false. Only one frame per moment survives.
- strength: 1-10 on the calibration scale.
- reason: one short line.
Be decisive: a typical strict pass keeps well under half of these candidates. Return every frame exactly once.`,
    { label: `judge:${c.c}`, phase: 'Judge', schema: JUDGE }),
  (j, c) => {
    if (!j) return null
    const demoted = j.frames.filter(f => !f.keep)
    if (!demoted.length) return { c: c.c, frames: j.frames, advocated: [] }
    return agent(`You are an independent photo editor acting as advocate for the photographer's own favorites. Another editor, curating a strict portfolio (theme in the "theme" field of ${c.index}), demoted the frames below.
The photographer's own 4-5 star frames in this chunk are listed in the "starred" array of ${c.index}. Consider ONLY the demoted frames that appear in that starred list; ignore the rest (if none are starred, return an empty verdicts list).
DEMOTED: ${JSON.stringify(demoted.map(f => ({ image_id: f.image_id, duplicate_of: f.duplicate_of, strength: f.strength, reason: f.reason })))}
View the calibration sheet ${calibration} (the archive's best frames), then open each demoted frame at ${THUMB} (and its duplicate_of frame when set) to compare.
Reinstate a frame only if it genuinely earns a portfolio place: it is strong on the calibration scale, OR it carries meaning the editor missed, OR the duplicate call is wrong (different moment/pose). Do not reinstate a true weaker duplicate. Be honest, not sentimental. Return a verdict for every listed frame.`,
      { label: `advocate:${c.c}`, phase: 'Advocate', schema: ADVOCATE })
      .then(a => {
        const re = new Map(((a && a.verdicts) || []).filter(v => v.reinstate).map(v => [v.image_id, v.reason]))
        for (const f of j.frames) if (re.has(f.image_id)) { f.keep = true; f.duplicate_of = null; f.reason = 'reinstated: ' + re.get(f.image_id) }
        return { c: c.c, frames: j.frames, advocated: (a && a.verdicts) || [] }
      })
  },
)
const ok = results.filter(Boolean)
const failed = chunks.filter((_, i) => !results[i]).map(c => c.c)
if (failed.length) log(`WARNING: chunks without result: ${failed.join(', ')}`)
log(`${ok.reduce((n, r) => n + r.frames.filter(f => f.keep).length, 0)} kept of ${ok.reduce((n, r) => n + r.frames.length, 0)}`)
return { chunks: ok, failed }

export const meta = {
  name: 'library-pass',
  description: 'Editor + adversarial critic over every unreviewed hero/select frame across all years',
  phases: [
    { title: 'Edit', detail: 'editor per 4 contact sheets' },
    { title: 'Critique', detail: 'critic per batch challenges portfolio/private/duplicate/licensing calls' },
  ],
}

const { sets, per = 4 } = args
const THUMB = 'thumbnail for image id N is at /Volumes/G DRIVE/LR-RESCUE.noindex/thumbs/<N div 1000, zero-padded to 5 digits>/<N>.jpg (e.g. 425320 -> /Volumes/G DRIVE/LR-RESCUE.noindex/thumbs/00425/425320.jpg)'

const PHOTO = {
  type: 'object',
  properties: {
    image_id: { type: 'integer' },
    verdict: { type: 'string', enum: ['portfolio', 'backstock', 'skip'] },
    duplicate_of: { type: ['integer', 'null'], description: 'id of a better near-identical frame, else null' },
    private: { type: 'boolean' },
    people: { type: 'string', enum: ['none', 'incidental', 'recognizable'] },
    minors: { type: 'boolean' },
    title: { type: 'string' },
    caption: { type: 'string' },
    keywords: { type: 'array', items: { type: 'string' } },
    theme: { type: 'string', description: 'short series-candidate name, e.g. "Roller derby night" or "Winter dog walks"' },
    uses: { type: 'array', items: { type: 'string', enum: ['instagram', 'stock', 'print', 'web'] } },
    release_notes: { type: 'array', items: { type: 'string', enum: ['needs_model_release', 'needs_property_release', 'contains_logos_or_brands', 'editorial_only'] } },
    crop: { type: ['object', 'null'], properties: { left: { type: 'number' }, top: { type: 'number' }, right: { type: 'number' }, bottom: { type: 'number' } } },
    treatment: { type: 'object', properties: {
      settings: { type: 'object', description: 'DELTAS to add to the current look. Allowed keys only: Exposure2012 (-5..5), Contrast2012, Highlights2012, Shadows2012, Whites2012, Blacks2012, Clarity2012, Texture, Dehaze, Vibrance, Saturation, PostCropVignetteAmount (-100..100), GrainAmount (0..100)' },
      notes: { type: 'string', description: 'one line: intent of the edit, plus anything the plugin cannot do (e.g. "darken sky with a graduated filter", "try B&W", "remove exit sign")' } } },
    editor_score: { type: 'integer', minimum: 1, maximum: 10 },
    why: { type: 'string' },
  },
  required: ['image_id', 'verdict', 'duplicate_of', 'private', 'people', 'minors', 'title', 'caption', 'keywords', 'theme', 'uses', 'release_notes', 'crop', 'treatment', 'editor_score', 'why'],
}

const brief = (year, index) => `You are a senior photo editor with a great eye, curating a photographer's personal archive (Lightroom catalog, capture year ${year}).
The goal is not just a portfolio but a deep, well-tagged BACKSTOCK: great images worth developing, licensing, printing, or posting.
These frames were pre-selected by an aesthetic + personal-taste model plus the photographer's own picks and star ratings; your job is the human judgment layer.
Judge each frame by its genre: documentary, protest and event coverage by moment, emotion and story (not polish); portraits by expression and connection; landscapes and nature by light and composition; pets by character. Do not penalize gritty documentary work for being busy or unpolished.

Each contact sheet is a 3x3 grid; every tile is labeled "<slot>  #<image_id>  <w>x<h>". The index JSON at ${index} lists every sheet path and its slot->image_id map, plus "style_profile" describing how THIS photographer edits. What you see is the CURRENT render, which already includes any edits the photographer made.
You may open a single frame larger when a decision needs detail (focus, expression, crop): ${THUMB}.

For EVERY photo on your sheets return one record:
- verdict: "portfolio" (would hold up in a tightly curated portfolio or print; expect roughly 10-25% of these pre-selected frames), "backstock" (solid, usable, worth keeping and tagging), or "skip" (weak, or a near-duplicate of a better frame).
- duplicate_of: when two or more frames are near-identical (same moment/pose), keep the best one and set duplicate_of on the others (they should then be "skip").
- private: true for nudity, intimate or sexual content, underwear/lingerie/bathing shots, or photos of identity documents/cards. Private photos stay in the archive but never go public. Keep their title/caption/keywords neutral and non-descriptive.
- people/minors: recognizable faces mean any commercial (stock) use needs a model release; flag minors.
- title (<= 8 words, evocative but accurate), caption (one plain sentence), keywords (6-15 concrete lowercase terms: subjects, setting, activity, mood, colors, season).
- theme: a short reusable series name so the curator can group frames across sheets.
- uses + release_notes: be honest about licensing; brands/logos and recognizable people limit stock use.
- crop: normalized box on the image AS DISPLAYED (0..1, left<right, top<bottom) only when a crop clearly improves it (straighten nothing; no rotation); else null.
- treatment.settings: restrained DELTAS on top of the current look, rooted in the style_profile (e.g. Clarity2012 +10, PostCropVignetteAmount -20). Use {} if it already looks finished. treatment.notes: intent + any edit the plugin can't apply.
- editor_score 1-10 and why (<= 25 words).
Return ONLY the structured result, one record per photo, using the exact image ids from the labels/index.`

const SCHEMA = { type: 'object', properties: { photos: { type: 'array', items: PHOTO } }, required: ['photos'] }

const batches = []
for (const s of sets) for (let i = 0; i < s.n; i += per) batches.push({ name: s.name, index: s.index, b: [i, Math.min(i + per, s.n)] })
log(`${sets.length} sets -> ${batches.length} editor batches`)

const results = await pipeline(
  batches,
  (x) => agent(`${brief(x.name, x.index)}

YOUR SHEETS: indices ${x.b[0]} through ${x.b[1] - 1} (0-based) of the "sheets" array in ${x.index}. Read the index first, then view each of your sheet images with the Read tool.`,
    { label: `edit:${x.name}:${x.b[0]}`, phase: 'Edit', schema: SCHEMA }),
  (ed, x) => {
    if (!ed) return null
    const b = x.b, index = x.index
    return agent(`You are an independent, demanding photo-editing critic. Another editor reviewed contact sheets ${b[0]}..${b[1] - 1} (0-based) from the "sheets" array in ${index} and produced the records below. View the same sheet images yourself (Read tool; open individual frames if needed: ${THUMB}).

Challenge, do not rubber-stamp. Check specifically:
1. Every "portfolio" call: would a picky editor really put it in a tight portfolio? Demote to "backstock" if not.
2. Privacy: any nudity/intimate/underwear/bathing/ID-document frame NOT marked private (critical), or marked private for no reason.
3. Near-duplicates: only one of each near-identical group should survive; set duplicate_of + skip on the weaker ones.
4. Licensing: "stock" with recognizable people must carry needs_model_release; visible brands must carry contains_logos_or_brands; minors should not be "stock".
5. Crops that cut off something important.
Return ONLY corrections (an empty list if the records are right).

EDITOR RECORDS:
${JSON.stringify(ed.photos)}`, { label: `critic:${x.name}:${b[0]}`, phase: 'Critique', schema: {
      type: 'object', properties: { corrections: { type: 'array', items: { type: 'object', properties: {
        image_id: { type: 'integer' },
        field: { type: 'string', enum: ['verdict', 'private', 'duplicate_of', 'uses', 'release_notes', 'crop'] },
        value: {}, reason: { type: 'string' } }, required: ['image_id', 'field', 'value', 'reason'] } } },
      required: ['corrections'] } })
      .then(cr => {
        const byId = new Map(ed.photos.map(p => [p.image_id, p]))
        const applied = []
        for (const c of (cr && cr.corrections) || []) {
          const p = byId.get(c.image_id)
          if (!p) continue
          p[c.field] = c.value
          if (c.field === 'duplicate_of' && c.value) p.verdict = 'skip'
          applied.push(c)
        }
        return { photos: ed.photos, corrections: applied }
      })
  },
)

const ok = results.filter(Boolean)
const failed = batches.filter((_, i) => !results[i]).map(x => `${x.name}:${x.b[0]}`)
if (failed.length) log(`WARNING: batches with no result: ${failed.join(', ')}`)
const photos = ok.flatMap((r, i) => r.photos)
log(`${photos.length} photos edited; ${ok.reduce((n, r) => n + r.corrections.length, 0)} critic corrections`)
return { photos, corrections: ok.flatMap(r => r.corrections), failed_batches: failed }

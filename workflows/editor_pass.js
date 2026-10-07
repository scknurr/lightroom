export const meta = {
  name: 'editor-pass',
  description: 'Photo-editor agents review contact sheets of top-scored frames, critics challenge calls, curator builds series',
  phases: [
    { title: 'Edit', detail: 'editor per 3 contact sheets: verdict, title, keywords, crop, treatment, uses' },
    { title: 'Critique', detail: 'independent critic per batch challenges portfolio/private/duplicate/licensing calls' },
    { title: 'Curate', detail: 'group into series and order an Instagram queue' },
  ],
}

const { index, n, year, priorFile = null } = args
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

const EDITOR_BRIEF = `You are a senior photo editor with a great eye, curating a photographer's personal archive (Lightroom catalog, capture year ${year}).
The goal is not just a portfolio but a deep, well-tagged BACKSTOCK: great images worth developing, licensing, printing, or posting.
These frames were pre-selected by an aesthetic + personal-taste model as the top ~6% of the year; your job is the human judgment layer.

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
for (let i = 0; i < n; i += 3) batches.push([i, Math.min(i + 3, n)])
log(`${n} sheets -> ${batches.length} editor batches`)

const results = await pipeline(
  batches,
  (b, _orig, k) => agent(`${EDITOR_BRIEF}

YOUR SHEETS: indices ${b[0]} through ${b[1] - 1} (0-based) of the "sheets" array in ${index}. Read the index first, then view each of your sheet images with the Read tool.`,
    { label: `edit:${b[0]}-${b[1] - 1}`, phase: 'Edit', schema: SCHEMA }),
  (ed, b) => {
    if (!ed) return null
    return agent(`You are an independent, demanding photo-editing critic. Another editor reviewed contact sheets ${b[0]}..${b[1] - 1} (0-based) from the "sheets" array in ${index} and produced the records below. View the same sheet images yourself (Read tool; open individual frames if needed: ${THUMB}).

Challenge, do not rubber-stamp. Check specifically:
1. Every "portfolio" call: would a picky editor really put it in a tight portfolio? Demote to "backstock" if not.
2. Privacy: any nudity/intimate/underwear/bathing/ID-document frame NOT marked private (critical), or marked private for no reason.
3. Near-duplicates: only one of each near-identical group should survive; set duplicate_of + skip on the weaker ones.
4. Licensing: "stock" with recognizable people must carry needs_model_release; visible brands must carry contains_logos_or_brands; minors should not be "stock".
5. Crops that cut off something important.
Return ONLY corrections (an empty list if the records are right).

EDITOR RECORDS:
${JSON.stringify(ed.photos)}`, { label: `critic:${b[0]}-${b[1] - 1}`, phase: 'Critique', schema: {
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
const photos = ok.flatMap(r => r.photos)
const corrections = ok.flatMap(r => r.corrections)
const failed = batches.filter((_, i) => !results[i]).map(b => `${b[0]}-${b[1] - 1}`)
if (failed.length) log(`WARNING: batches with no result: ${failed.join(', ')}`)
log(`${photos.length} photos edited; ${corrections.length} critic corrections`)

phase('Curate')
const condensed = photos.filter(p => p.verdict !== 'skip' && !p.private).map(p => ({
  id: p.image_id, v: p.verdict, s: p.editor_score, theme: p.theme, title: p.title,
  kw: p.keywords.slice(0, 6), people: p.people, minors: p.minors, uses: p.uses,
}))
const curation = await agent(`You are the curator for a photographer's ${year} archive. Below are the non-private keepers from the editor pass (photos are in capture-time order). Build:
1. series: coherent "chunks" worth presenting together (an event, a place, a recurring subject, a visual style). Merge editor themes that mean the same thing. Each series: name (<= 5 words), description (one sentence), members (image ids, 4-60), cover_id (the strongest member), best_for (subset of instagram, stock, print, web). A photo may belong to more than one series. Skip one-offs.
2. instagram_queue: an ordered list of up to 60 image ids drawn ONLY from verdict "portfolio" photos whose uses include instagram, sequenced for a varied, engaging feed (alternate subjects/colors; never two from the same series back-to-back).
3. notes: 3-6 short bullet observations about this year's strongest material and what is worth developing first.

${priorFile ? `ALSO include the keepers from an earlier editor pass of the same year, stored as a JSON list of the same record shape at ${priorFile} (read it with the Read tool); series and the queue must cover BOTH sets.\n\n` : ''}KEEPERS (JSON): ${JSON.stringify(condensed)}`, { label: 'curator', phase: 'Curate', schema: {
  type: 'object', properties: {
    series: { type: 'array', items: { type: 'object', properties: {
      name: { type: 'string' }, description: { type: 'string' }, members: { type: 'array', items: { type: 'integer' } },
      cover_id: { type: 'integer' }, best_for: { type: 'array', items: { type: 'string' } } },
      required: ['name', 'description', 'members', 'cover_id', 'best_for'] } },
    instagram_queue: { type: 'array', items: { type: 'integer' } },
    notes: { type: 'array', items: { type: 'string' } } },
  required: ['series', 'instagram_queue', 'notes'] } })

return { year, photos, corrections, failed_batches: failed, ...(curation || { series: [], instagram_queue: [], notes: ['curator failed'] }) }

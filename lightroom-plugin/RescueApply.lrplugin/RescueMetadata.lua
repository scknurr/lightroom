--[[----------------------------------------------------------------------------
RescueMetadata.lua - plugin-defined photo metadata (LrMetadataProvider)

  rescueRole    on virtual copies this plugin created:
                'rescue-edit-pending' right after creation (own write gate),
                'rescue-edit' once developed, 'rescue-orphan' for a copy made
                of the wrong photo. Survives the user renaming the copy, so
                re-runs never create a second copy.
  rescueRunId   run_id of the manifest that developed that copy.
  rescueLastRun run_id of the last manifest that changed a master's metadata
                (lets you find everything a run touched via the Library filter).

Field ids are permanent: changing them needs a schemaVersion bump.
------------------------------------------------------------------------------]]

return {
  metadataFieldsForPhotos = {
    {
      id = 'rescueRole',
      title = 'Rescue role',
      dataType = 'string',
      readOnly = true,
      searchable = true,
      browsable = true,
    },
    {
      id = 'rescueRunId',
      title = 'Rescue copy run',
      dataType = 'string',
      readOnly = true,
      searchable = true,
      browsable = true,
    },
    {
      id = 'rescueLastRun',
      title = 'Rescue last run',
      dataType = 'string',
      readOnly = true,
      searchable = true,
      browsable = true,
    },
  },

  schemaVersion = 1,
}

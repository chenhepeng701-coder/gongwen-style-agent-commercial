export const SUPPORTED_DROP_EXTENSIONS = ['docx', 'txt', 'md']

export function isSupportedLibraryFile(fileName) {
  const ext = String(fileName || '').split('.').pop().toLowerCase()
  return SUPPORTED_DROP_EXTENSIONS.includes(ext)
}

export function collectLibraryFiles(files) {
  const result = {
    files: [],
    ignoredUnsupported: 0,
  }

  for (const file of Array.from(files || [])) {
    if (isSupportedLibraryFile(file.name)) {
      result.files.push(file)
    } else {
      result.ignoredUnsupported += 1
    }
  }

  return result
}

function toArray(list) {
  return Array.from({ length: list?.length || 0 }, (_, index) => list[index]).filter(Boolean)
}

function fileEntryToFile(entry) {
  return new Promise((resolve, reject) => {
    entry.file(resolve, reject)
  })
}

async function readDirectoryEntries(entry) {
  const reader = entry.createReader()
  const entries = []

  while (true) {
    const batch = await new Promise((resolve, reject) => {
      reader.readEntries(resolve, reject)
    })
    if (!batch.length) break
    entries.push(...batch)
  }

  return entries
}

async function readEntryRecursively(entry, state) {
  if (entry.isDirectory) {
    let entries
    try {
      entries = await readDirectoryEntries(entry)
    } catch {
      state.readFailed = true
      return
    }

    for (const child of entries) {
      await readEntryRecursively(child, state)
    }
    return
  }

  if (!isSupportedLibraryFile(entry.name)) {
    state.ignoredUnsupported += 1
    return
  }

  try {
    state.files.push(await fileEntryToFile(entry))
  } catch {
    state.readFailed = true
  }
}

export async function collectDroppedFiles(items, fallbackFiles = []) {
  const state = {
    files: [],
    ignoredUnsupported: 0,
    readFailed: false,
  }

  const dataTransferItems = toArray(items)
  const entries = dataTransferItems
    .map((item) => item.webkitGetAsEntry?.())
    .filter(Boolean)

  if (entries.length > 0) {
    for (const entry of entries) {
      await readEntryRecursively(entry, state)
    }
    return state
  }

  const fallbackResult = collectLibraryFiles(fallbackFiles)
  state.files.push(...fallbackResult.files)
  state.ignoredUnsupported += fallbackResult.ignoredUnsupported

  return state
}

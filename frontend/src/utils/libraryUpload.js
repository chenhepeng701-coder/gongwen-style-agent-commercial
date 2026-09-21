function getUploadFailureMessage(file, errorOrResponse) {
  const fileName = file?.name || 'unknown'
  const reason = errorOrResponse?.message || '上传失败'
  return `${fileName}: ${reason}`
}

export function formatImportResultMessage({ ok, fail, ignored }) {
  return `导入完成：成功 ${ok} 篇，失败 ${fail} 篇，忽略 ${ignored} 个不支持文件。`
}

export async function batchUploadLibraryFiles(files, {
  uploadFile,
  onProgress,
  concurrency = 5,
} = {}) {
  let ok = 0
  let fail = 0
  const errors = []
  const safeFiles = Array.from(files || []).filter(Boolean)
  const total = safeFiles.length
  const pool = new Set()

  const updateProgress = () => {
    onProgress?.({ ok, fail, total, errors: [...errors] })
  }

  const uploadOne = async (file) => {
    try {
      const res = await uploadFile(file)
      if (res?.success === false) {
        fail += 1
        errors.push(getUploadFailureMessage(file, res))
      } else {
        ok += 1
      }
    } catch (error) {
      fail += 1
      errors.push(getUploadFailureMessage(file, error))
    }
    updateProgress()
  }

  for (const file of safeFiles) {
    const task = uploadOne(file).finally(() => pool.delete(task))
    pool.add(task)
    if (pool.size >= concurrency) {
      await Promise.race(pool)
    }
  }

  await Promise.all(pool)
  return { ok, fail, total, errors }
}

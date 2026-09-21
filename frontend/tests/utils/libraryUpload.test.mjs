import assert from 'node:assert/strict'
import { batchUploadLibraryFiles, formatImportResultMessage } from '../../src/utils/libraryUpload.js'

async function testResolvedUploadWithoutSuccessFlagCountsAsSuccess() {
  const files = [{ name: 'a.docx' }, { name: 'b.docx' }]
  const progress = []

  const result = await batchUploadLibraryFiles(files, {
    uploadFile: async () => ({ message: '入库成功' }),
    onProgress: (snapshot) => progress.push(snapshot),
    concurrency: 2,
  })

  assert.equal(result.ok, 2)
  assert.equal(result.fail, 0)
  assert.equal(progress.at(-1).ok, 2)
}

async function testExplicitSuccessFalseCountsAsFailureWithReason() {
  const result = await batchUploadLibraryFiles([{ name: 'bad.docx' }], {
    uploadFile: async () => ({ success: false, message: '解析失败' }),
  })

  assert.equal(result.ok, 0)
  assert.equal(result.fail, 1)
  assert.deepEqual(result.errors, ['bad.docx: 解析失败'])
}

async function testRejectedUploadCountsAsFailureWithReason() {
  const result = await batchUploadLibraryFiles([{ name: 'bad.md' }], {
    uploadFile: async () => {
      throw new Error('服务端错误')
    },
  })

  assert.equal(result.ok, 0)
  assert.equal(result.fail, 1)
  assert.deepEqual(result.errors, ['bad.md: 服务端错误'])
}

function testImportResultMessageIncludesIgnoredCount() {
  const message = formatImportResultMessage({ ok: 2, fail: 1, ignored: 3 })

  assert.equal(message, '导入完成：成功 2 篇，失败 1 篇，忽略 3 个不支持文件。')
}

await testResolvedUploadWithoutSuccessFlagCountsAsSuccess()
await testExplicitSuccessFalseCountsAsFailureWithReason()
await testRejectedUploadCountsAsFailureWithReason()
testImportResultMessageIncludesIgnoredCount()
console.log('libraryUpload tests passed')

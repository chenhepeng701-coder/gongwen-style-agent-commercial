import assert from 'node:assert/strict'
import { collectDroppedFiles, collectLibraryFiles } from '../../src/utils/dropFiles.js'

function fileEntry(name, options = {}) {
  return {
    isFile: true,
    isDirectory: false,
    name,
    file(resolve, reject) {
      if (options.fail) reject(new Error('read failed'))
      else resolve({ name })
    },
  }
}

function directoryEntry(name, batches) {
  let index = 0
  return {
    isFile: false,
    isDirectory: true,
    name,
    createReader() {
      return {
        readEntries(resolve) {
          resolve(batches[index++] || [])
        },
      }
    },
  }
}

function itemFor(entry) {
  return {
    webkitGetAsEntry() {
      return entry
    },
  }
}

async function testRecursiveSupportedFiles() {
  const root = directoryEntry('root', [[
    fileEntry('a.docx'),
    fileEntry('ignore.pdf'),
    directoryEntry('child', [[fileEntry('b.txt'), fileEntry('c.md')], []]),
  ], []])

  const result = await collectDroppedFiles([itemFor(root)])

  assert.deepEqual(result.files.map((file) => file.name), ['a.docx', 'b.txt', 'c.md'])
  assert.equal(result.ignoredUnsupported, 1)
  assert.equal(result.readFailed, false)
}

async function testNonIterableDataTransferItemList() {
  const items = {
    0: itemFor(fileEntry('one.docx')),
    1: itemFor(fileEntry('two.md')),
    length: 2,
  }

  const result = await collectDroppedFiles(items)

  assert.deepEqual(result.files.map((file) => file.name), ['one.docx', 'two.md'])
}

async function testReadFailureDoesNotDropOtherFiles() {
  const root = directoryEntry('root', [[fileEntry('ok.docx'), fileEntry('blocked.docx', { fail: true })], []])

  const result = await collectDroppedFiles([itemFor(root)])

  assert.deepEqual(result.files.map((file) => file.name), ['ok.docx'])
  assert.equal(result.readFailed, true)
}

function testCollectLibraryFilesFiltersFolderInputFiles() {
  const result = collectLibraryFiles([
    { name: 'a.docx', webkitRelativePath: 'folder/a.docx' },
    { name: 'b.txt', webkitRelativePath: 'folder/b.txt' },
    { name: 'c.md', webkitRelativePath: 'folder/child/c.md' },
    { name: 'ignore.pdf', webkitRelativePath: 'folder/ignore.pdf' },
  ])

  assert.deepEqual(result.files.map((file) => file.name), ['a.docx', 'b.txt', 'c.md'])
  assert.equal(result.ignoredUnsupported, 1)
}

await testRecursiveSupportedFiles()
await testNonIterableDataTransferItemList()
await testReadFailureDoesNotDropOtherFiles()
testCollectLibraryFilesFiltersFolderInputFiles()
console.log('dropFiles tests passed')

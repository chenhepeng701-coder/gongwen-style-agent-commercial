import test from 'node:test'
import assert from 'node:assert/strict'

import api, { publicErrorMessage } from '../../src/api.js'

function axiosError(status, detail) {
  return {
    response: {
      status,
      statusText: 'Server Error',
      data: { detail },
    },
  }
}

test('MODEL_TIMEOUT is shown without generic 500 replacement', () => {
  const message = publicErrorMessage(axiosError(502, {
    success: false,
    error_code: 'MODEL_TIMEOUT',
    message: 'AI模型响应超时，请稍后重试。',
  }))

  assert.equal(message, 'AI模型响应超时，请稍后重试。')
})

test('MODEL_CONNECTION_ERROR uses model configuration guidance', () => {
  const message = publicErrorMessage(axiosError(502, {
    success: false,
    error_code: 'MODEL_CONNECTION_ERROR',
    message: 'AI模型连接失败，请检查模型配置。',
  }))

  assert.equal(message, 'AI模型连接失败，请检查模型配置。')
})

test('FACT_SAFETY_BLOCKED explains safety blocking without claiming missing material', () => {
  const message = publicErrorMessage(axiosError(400, {
    success: false,
    error_code: 'FACT_SAFETY_BLOCKED',
    message: '模型生成内容包含无法由资料支持的事实，系统已停止交付。',
  }))

  assert.equal(message, '模型生成内容包含无法由资料支持的事实，系统已停止交付。')
})

test('MATERIAL_INSUFFICIENT remains distinct from fact safety blocking', () => {
  const message = publicErrorMessage(axiosError(400, {
    success: false,
    error_code: 'MATERIAL_INSUFFICIENT',
    message: '当前资料的真实事实容量不足，无法支持本次文章生成。',
  }))

  assert.equal(message, '当前资料的真实事实容量不足，无法支持本次文章生成。\n请补充相关参考材料后重新生成。')
})

test('INSUFFICIENT_EVIDENCE shows planning guidance instead of server error', () => {
  const message = publicErrorMessage(axiosError(422, {
    success: false,
    error_code: 'INSUFFICIENT_EVIDENCE',
    message: '当前资料不足以支撑安全生成，请补充资料或调整资料范围。',
    stage: 'PLANNING',
    retryable: true,
    preserve_form: true,
  }))

  assert.equal(message, '当前资料不足以支撑安全生成，请补充资料或调整资料范围。')
})

test('INSUFFICIENT_EVIDENCE interceptor preserves retry and form metadata', async () => {
  await assert.rejects(
    api.get('/generation/generate', {
      adapter: () => Promise.reject(axiosError(422, {
        success: false,
        error_code: 'INSUFFICIENT_EVIDENCE',
        message: '当前资料不足以支撑安全生成，请补充资料或调整资料范围。',
        stage: 'PLANNING',
        retryable: true,
        preserve_form: true,
      })),
    }),
    (error) => error.message === '当前资料不足以支撑安全生成，请补充资料或调整资料范围。'
      && error.errorCode === 'INSUFFICIENT_EVIDENCE'
      && error.stage === 'PLANNING'
      && error.retryable === true
      && error.preserveForm === true,
  )
})

test('FACT_SAFETY_BLOCKED interceptor message has no server error prefix', async () => {
  await assert.rejects(
    api.get('/generation/generate', {
      adapter: () => Promise.reject(axiosError(400, {
        success: false,
        error_code: 'FACT_SAFETY_BLOCKED',
        message: '模型生成内容包含无法由资料支持的事实，系统已停止交付。',
      })),
    }),
    {
      message: '模型生成内容包含无法由资料支持的事实，系统已停止交付。',
    },
  )
})

test('client timeout is reported as model timeout instead of connection failure', async () => {
  const expected = publicErrorMessage(axiosError(502, {
    error_code: 'MODEL_TIMEOUT',
  }))

  await assert.rejects(
    api.get('/generation/generate', {
      adapter: () => Promise.reject({ code: 'ECONNABORTED', request: {} }),
    }),
    (error) => error.message === expected,
  )
})

test('INTERNAL_ERROR uses the public internal error message', () => {
  const message = publicErrorMessage(axiosError(500, {
    success: false,
    error_code: 'INTERNAL_ERROR',
    message: '服务器内部错误，请稍后重试。',
  }))

  assert.equal(message, '服务器内部错误，请稍后重试。')
})

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { ImageLightbox } from '@/components/chat/zoomable-image'

import { Zoomable } from './zoomable'

afterEach(cleanup)

describe('zoomable dialogs', () => {
  it('names the image lightbox from its alt text', () => {
    render(
      <ImageLightbox
        alt="Campaign image"
        copy={{ downloadImage: 'Download image', openImage: 'Open image', savingImage: 'Saving image' }}
        onClick={() => {}}
        onOpenChange={() => {}}
        open
        saving={false}
        src="data:image/png;base64,aA=="
      />
    )

    expect(screen.getByRole('dialog', { name: 'Campaign image' })).toBeDefined()
  })

  it('uses the expand label as the full-view dialog name', () => {
    render(
      <Zoomable label="Open chart full view">
        <div>Chart</div>
      </Zoomable>
    )

    fireEvent.click(screen.getByRole('button', { name: 'Open chart full view' }))

    expect(screen.getByRole('dialog', { name: 'Open chart full view' })).toBeDefined()
  })
})

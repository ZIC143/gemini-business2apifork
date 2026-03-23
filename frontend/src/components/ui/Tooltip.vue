<template>
  <span
    ref="triggerRef"
    class="inline-flex"
    @mouseenter="showTooltip"
    @mouseleave="hideTooltip"
    @focusin="showTooltip"
    @focusout="hideTooltip"
  >
    <slot />
  </span>
  <Teleport to="body">
    <div
      v-if="visible"
      ref="tooltipRef"
      class="fixed z-[9999] -translate-x-1/2 -translate-y-full rounded-md bg-foreground px-2 py-1 text-[10px]
             text-background shadow-lg"
      :style="tooltipStyle"
    >
      {{ text }}
      <span
        class="absolute left-1/2 top-full h-0 w-0 -translate-x-1/2 border-x-4 border-t-4
               border-x-transparent border-t-foreground"
      ></span>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps<{
  text: string
  offset?: number
}>()

const triggerRef = ref<HTMLElement | null>(null)
const tooltipRef = ref<HTMLElement | null>(null)
const visible = ref(false)
const tooltipStyle = ref<Record<string, string>>({})

const updatePosition = () => {
  if (!triggerRef.value) return
  const rect = triggerRef.value.getBoundingClientRect()
  const offset = props.offset ?? 8
  const viewportPadding = 8
  const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0
  const cardWidth = tooltipRef.value?.offsetWidth || 160
  const center = rect.left + rect.width / 2
  const minCenter = viewportPadding + cardWidth / 2
  const maxCenter = Math.max(minCenter, viewportWidth - viewportPadding - cardWidth / 2)
  const clampedCenter = Math.min(maxCenter, Math.max(minCenter, center))

  tooltipStyle.value = {
    left: `${clampedCenter}px`,
    top: `${rect.top - offset}px`,
    maxWidth: `calc(100vw - ${viewportPadding * 2}px)`,
  }
}

const showTooltip = () => {
  visible.value = true
  nextTick(() => {
    updatePosition()
    requestAnimationFrame(updatePosition)
  })
}

const hideTooltip = () => {
  visible.value = false
}

const handleViewportChange = () => {
  if (!visible.value) return
  updatePosition()
}

onMounted(() => {
  window.addEventListener('resize', handleViewportChange)
  window.addEventListener('scroll', handleViewportChange, true)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleViewportChange)
  window.removeEventListener('scroll', handleViewportChange, true)
})
</script>

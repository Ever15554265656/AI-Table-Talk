<script setup>
// 根组件只做路由出口。页面各自负责"容器内滚动"，body 永不滚动（PRD AC-1.9 / AC-6.5）。
</script>

<template>
  <router-view v-slot="{ Component }">
    <component :is="Component" />
  </router-view>
</template>

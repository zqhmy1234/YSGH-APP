# HensSvg 组件使用文档

一个专为 uni-app-x 设计的 SVG 图标组件，支持多种 SVG 输入方式，提供高性能的原生渲染。

## 快速开始

### 基础引入

```vue
<template>
  <view>
    <hens-svg
      :src="svgContent"
      :width="100"
      :height="100"
      color="#ff0000"
    />
  </view>
</template>

<script setup>
const svgContent = `
<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
  <path d="M12 2L13.09 8.26L22 9L13.09 9.74L12 16L10.91 9.74L2 9L10.91 8.26L12 2Z"/>
</svg>
`
</script>
```

## 使用方式

### 1. 完整 SVG 内容渲染

传入完整的 SVG 文件内容，自动解析所有路径：

```vue
<template>
  <view>
    <hens-svg
      :src="svgContent"
      :width="200"
      :height="200"
      color="#666666"
    />
  </view>
</template>

<script setup>
const svgContent = `
<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
  <path d="M12 2L13.09 8.26L22 9L13.09 9.74L12 16L10.91 9.74L2 9L10.91 8.26L12 2Z"/>
  <path d="M8 17L9 20L12 18L15 20L16 17"/>
</svg>
`
</script>
```

### 2. 文件路径加载

```vue
<template>
  <view>
    <hens-svg
      src="/static/icons/home.svg"
      :width="32"
      :height="32"
      color="#333333"
    />
  </view>
</template>
```

## Props 属性

| 属性 | 类型 | 默认值 | 必填 | 说明 |
|------|------|--------|------|------|
| src | String | - | ✅ | SVG 源（自动检测：URL/文件路径/SVG内容） |
| size | Number | 100 | ❌ | 组件尺寸快捷设置（等宽高，单位：px） |
| width | Number | 0 | ❌ | 组件宽度（单位：px，优先级高于size） |
| height | Number | 0 | ❌ | 组件高度（单位：px，优先级高于size） |
| color | String | '' | ❌ | 填充颜色（十六进制或CSS颜色名） |
| opacity | Number | 1.0 | ❌ | 透明度（0-1） |
| backgroundColor | String | '' | ❌ | 背景颜色 |
| aspectRatio | String | 'fit' | ❌ | 纵横比策略：'fill'、'fit'、'stretch' |
| cache | Boolean | true | ❌ | 是否启用缓存 |
| timeout | Number | 10000 | ❌ | 网络请求超时时间（毫秒） |
| retryAttempts | Number | 1 | ❌ | 重试次数 |

### 属性详解

#### 尺寸设置
- 当 `width` 或 `height` > 0 时，优先使用具体的宽高值
- 当 `width` 或 `height` = 0 时，使用 `size` 作为该维度的尺寸
- 单位都是像素（px），不是 rpx

#### aspectRatio 纵横比策略
- **fit**（默认）：保持原始比例，完整显示在容器内
- **fill**：保持原始比例，填充整个容器（可能裁剪）
- **stretch**：拉伸填充容器（可能变形）

#### 缓存机制
- `cache: true` 时，相同的 src 会被缓存，提升加载性能
- 对于频繁使用的图标建议启用缓存

## Events 事件

| 事件名 | 参数 | 说明 |
|--------|------|------|
| load | - | SVG 加载成功时触发 |
| error | SvgError | SVG 加载失败时触发 |
| loading | boolean | 加载状态变化时触发（true: 开始加载，false: 加载完成） |

## 颜色设置

组件支持多种颜色格式：

```vue
<template>
  <view class="color-demo">
    <!-- 十六进制颜色 -->
    <hens-svg :src="iconSvg" color="#ff0000" />
    <hens-svg :src="iconSvg" color="#3498db" />
    
    <!-- 短格式十六进制 -->
    <hens-svg :src="iconSvg" color="#f00" />
    <hens-svg :src="iconSvg" color="#34b" />
    
    <!-- 不设置颜色，使用 SVG 原始颜色 -->
    <hens-svg :src="iconSvg" />
  </view>
</template>
```

## 响应式设计

```vue
<template>
  <view class="responsive-icons">
    <!-- 小尺寸图标 -->
    <hens-svg :src="iconSvg" :size="24" />
    
    <!-- 中等尺寸图标 -->
    <hens-svg :src="iconSvg" :size="48" />
    
    <!-- 大尺寸图标 -->
    <hens-svg :src="iconSvg" :size="96" />
    
    <!-- 不同宽高比 -->
    <hens-svg :src="iconSvg" :width="60" :height="40" />
  </view>
</template>

<style>
.responsive-icons {
  display: flex;
  align-items: center;
  gap: 20rpx;
}
</style>
```

## 常见 SVG 内容示例

### 基础图标

```vue
<template>
  <view class="icon-examples">
    <!-- 首页图标 -->
    <hens-svg
      :src="homeIcon"
      :size="48" color="#666"
    />
    
    <!-- 设置图标 -->
    <hens-svg
      :src="settingsIcon"
      :size="48" color="#666"
    />
  </view>
</template>

<script setup>
const homeIcon = `
<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
  <path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/>
</svg>
`

const settingsIcon = `
<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
  <path d="M19.14,12.94c0.04-0.3,0.06-0.61,0.06-0.94c0-0.32-0.02-0.64-0.07-0.94l2.03-1.58c0.18-0.14,0.23-0.41,0.12-0.61 l-1.92-3.32c-0.12-0.22-0.37-0.29-0.59-0.22l-2.39,0.96c-0.5-0.38-1.03-0.7-1.62-0.94L14.4,2.81c-0.04-0.24-0.24-0.41-0.48-0.41 h-3.84c-0.24,0-0.43,0.17-0.47,0.41L9.25,5.35C8.66,5.59,8.12,5.92,7.63,6.29L5.24,5.33c-0.22-0.08-0.47,0-0.59,0.22L2.74,8.87 C2.62,9.08,2.66,9.34,2.86,9.48l2.03,1.58C4.84,11.36,4.8,11.69,4.8,12s0.02,0.64,0.07,0.94l-2.03,1.58 c-0.18,0.14-0.23,0.41-0.12,0.61l1.92,3.32c0.12,0.22,0.37,0.29,0.59,0.22l2.39-0.96c0.5,0.38,1.03,0.7,1.62,0.94l0.36,2.54 c0.05,0.24,0.24,0.41,0.48,0.41h3.84c0.24,0,0.44-0.17,0.47-0.41l0.36-2.54c0.59-0.24,1.13-0.56,1.62-0.94l2.39,0.96 c0.22,0.08,0.47,0,0.59-0.22l1.92-3.32c0.12-0.22,0.07-0.47-0.12-0.61L19.14,12.94z M12,15.6c-1.98,0-3.6-1.62-3.6-3.6 s1.62-3.6,3.6-3.6s3.6,1.62,3.6,3.6S13.98,15.6,12,15.6z"/>
</svg>
`
</script>
```

## 性能优化建议

### 1. 合理设置尺寸

```vue
<!-- 推荐：根据实际显示需求设置尺寸 -->
<hens-svg :src="iconSvg" :size="32" />

<!-- 不推荐：过大的尺寸浪费性能 -->
<hens-svg :src="iconSvg" :size="500" />
```

### 2. 重用相同图标

```vue
<script setup>
// 定义常用 SVG 图标内容
const iconLibrary = {
  home: `<svg viewBox="0 0 24 24"><path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/></svg>`,
  settings: `<svg viewBox="0 0 24 24"><path d="M12 15.5A3.5 3.5 0 0 1 8.5 12A3.5 3.5 0 0 1 12 8.5a3.5 3.5 0 0 1 3.5 3.5 3.5 3.5 0 0 1-3.5 3.5m7.43-2.53c.04-.32.07-.64.07-.97 0-.33-.03-.66-.07-1l1.86-1.41c.18-.14.23-.41.12-.61l-1.74-3.18a.5.5 0 0 0-.61-.22l-2.4.96c-.5-.38-1.03-.7-1.62-.94L14.4,2.81c-.04-.24-.24-.41-.48-.41h-3.84c-.24,0-.43.17-.47.41L9.25,5.35C8.66,5.59,8.12,5.92,7.63,6.29L5.24,5.33c-.22-.08-.47,0-.59.22L2.74,8.87C2.62,9.08,2.66,9.34,2.86,9.48l1.86,1.41C4.84,11.36,4.8,11.69,4.8,12s0.02,0.64,0.07,0.94l-1.86,1.41c-0.18,0.14-0.23,0.41-0.12,0.61l1.74,3.18c0.12,0.22,0.37,0.29,0.59,0.22l2.4-0.96c0.5,0.38,1.03,0.7,1.62,0.94l0.36,2.54c0.05,0.24,0.24,0.41,0.48,0.41h3.84c0.24,0,0.44-0.17,0.47-0.41l0.36-2.54c0.59-0.24,1.13-0.56,1.62-0.94l2.4,0.96c0.22,0.08,0.47,0,0.59-0.22l1.74-3.18c0.12-0.22,0.07-0.47-0.12-0.61L19.43,12.97z"/></svg>`,
  user: `<svg viewBox="0 0 24 24"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>`
}
</script>

<template>
  <view>
    <hens-svg :src="iconLibrary.home" :size="24" />
    <hens-svg :src="iconLibrary.settings" :size="24" />
  </view>
</template>
```

## 故障排除

### 常见问题

**Q: 图标不显示？**
A: 检查 src 属性是否正确，确保 SVG 内容格式正确或文件路径存在。

**Q: 颜色设置无效？**
A: 确保使用十六进制颜色格式（#RRGGBB），部分复杂 SVG 可能包含自定义颜色属性。

**Q: 图标显示不完整？**
A: 检查 width 和 height 设置是否合适，某些 SVG 内容可能需要特定的宽高比。

### 调试技巧

1. 使用在线 SVG 编辑器验证 SVG 内容正确性
2. 检查控制台是否有错误信息
3. 尝试简化 SVG 内容进行测试
4. 验证文件路径是否正确（对于 file 类型）

## 技术实现

- **原生渲染**: 基于 iOS SVGKit 和 Android Vector Drawable 进行原生矢量渲染
- **UTS 架构**: 使用 UTS + Swift/Kotlin 混编实现跨平台支持
- **高效解析**: 采用原生解析器处理 SVG 内容和文件加载
- **向后兼容**: 保持对现有 API 的完全兼容

## 版本兼容性

- uni-app-x 4.0+
- iOS 10.0+
- Android API 21+
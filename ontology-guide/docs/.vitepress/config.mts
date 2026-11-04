import { defineConfig } from 'vitepress'

// https://vitepress.dev/reference/site-config
export default defineConfig({
  lang: 'zh-CN',
  title: "本体建模与应用指南",
  description: "从概念体系到形式化模型，再到数据与应用",
  base: "/",
  themeConfig: {
    // https://vitepress.dev/reference/default-theme-config
    nav: [
      { text: '首页', link: '/' },
      { text: '总览', link: '/main' },
      { text: '标准与语言', link: '/spec/' },
      { text: '建模', link: '/modeling/' },
      { text: '实现', link: '/impl/' },
      { text: '应用', link: '/application/' },
      { text: '端到端案例', link: '/examples/' },
      { text: 'DDD 指南', link: 'https://ddd-fans.github.io/ddd-guideline/' }
    ],

    sidebar: [
      {
        text: '本体建模与应用指南',
        items: [
          { text: '总览', link: '/main' },
          { text: '本体与语义网历史', link: '/history' },
          { text: '本体模型', link: '/ontology-model' },
          {
            text: '标准与语言',
            link: '/spec/',
            items: [
              { text: 'RDF 数据模型', link: '/spec/rdf' },
              { text: 'OWL 2 本体语言', link: '/spec/owl' },
              { text: 'SKOS 词表', link: '/spec/skos' },
              { text: 'SHACL 形状约束', link: '/spec/shacl' },
              { text: 'SPARQL 查询', link: '/spec/sparql' }
            ]
          },
          {
            text: '建模',
            link: '/modeling/',
            items: [
              {
                text: '方法论',
                link: '/modeling/method/',
                items: [
                  { text: '能力问题（CQ）', link: '/modeling/method/competency-questions' },
                  { text: '建模流程与规范', link: '/modeling/method/process' },
                  { text: '质量评估与反模式', link: '/modeling/method/quality' }
                ]
              },
              {
                text: '本体元素',
                link: '/modeling/elements/',
                items: [
                  { text: '概念与类', link: '/modeling/elements/concept' },
                  { text: '对象属性与关系', link: '/modeling/elements/relation' },
                  { text: '数据属性与取值', link: '/modeling/elements/attribute' },
                  { text: '个体与实例数据', link: '/modeling/elements/individual' },
                  { text: '公理', link: '/modeling/elements/axiom' },
                  { text: '约束与业务规则', link: '/modeling/elements/constraint' },
                  { text: '模块化与复用', link: '/modeling/elements/module' }
                ]
              },
              { text: '本体设计模式', link: '/modeling/patterns' }
            ]
          },
          {
            text: '实现',
            link: '/impl/',
            items: [
              {
                text: '架构模式',
                link: '/impl/arch-pattern/',
                items: [
                  { text: '分层与六边形架构', link: '/impl/arch-pattern/layered' },
                  { text: '存储选型', link: '/impl/arch-pattern/storage' },
                  { text: '推理与物化', link: '/impl/arch-pattern/inference' },
                  { text: '版本与演进', link: '/impl/arch-pattern/versioning' }
                ]
              },
              { text: '实现本体对象', link: '/impl/objects/' },
              {
                text: '技术栈',
                link: '/impl/tech/',
                items: [
                  { text: 'JVM 技术栈', link: '/impl/tech/jvm' },
                  { text: 'Python 技术栈', link: '/impl/tech/python' },
                  { text: 'JavaScript/TypeScript 技术栈', link: '/impl/tech/js' }
                ]
              }
            ]
          },
          {
            text: '应用',
            link: '/application/',
            items: [
              { text: '知识图谱构建', link: '/application/knowledge-graph' },
              { text: '语义检索与 RAG', link: '/application/semantic-search' },
              { text: '数据治理与主数据', link: '/application/data-governance' },
              { text: '业务规则与校验', link: '/application/rules-validation' },
              { text: '语义集成与互操作', link: '/application/integration' },
              { text: '运营与生命周期', link: '/application/lifecycle' }
            ]
          },
          { text: '落地路线图', link: '/adoption/' },
          { text: '端到端案例', link: '/examples/' }
        ]
      }
    ],

    outline: {
      level: [2, 3],
      label: '本页目录'
    },
    docFooter: {
      prev: '上一篇',
      next: '下一篇'
    }
  }
})

module.exports = function (eleventyConfig) {
  eleventyConfig.addPassthroughCopy({ "src/assets": "assets" });

  return {
    dir: {
      input: "src",
      includes: "_includes",
      data: "_data",
      output: "../papr"
    },
    htmlTemplateEngine: "njk",
    markdownTemplateEngine: "njk"
  };
};

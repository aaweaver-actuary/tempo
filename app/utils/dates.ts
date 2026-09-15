export const dateLabel = new Intl.DateTimeFormat("en-US", {
  month: "long",
  day: "numeric",
}).format(new Date());

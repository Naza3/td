#include <stdio.h>
#include <string.h>
#include <td/telegram/td_json_client.h>

int main(void) {
  const char *response = td_execute("{\"@type\":\"getTextEntities\",\"text\":\"https://telegram.org\"}");
  if (!response || !strstr(response, "\"textEntities\"") || !strstr(response, "\"textEntityTypeUrl\"")) {
    fprintf(stderr, "Unexpected getTextEntities response: %s\n", response ? response : "NULL");
    return 1;
  }
  response = td_execute("{\"@type\":\"getOption\",\"name\":\"version\"}");
  if (!response || !strstr(response, "\"optionValueString\"") || !strstr(response, "\"value\":")) {
    fprintf(stderr, "Unexpected TDLib version response: %s\n", response ? response : "NULL");
    return 1;
  }
  puts(response);
  puts("Installed CMake SDK consumer check passed.");
  return 0;
}

package language

import (
	"database/sql/driver"
	"encoding/json"
	"strings"
)

type Alpha2Code string

func (code *Alpha2Code) UnmarshalJSON(data []byte) error {
	var str string
	if err := json.Unmarshal(data, &str); err != nil {
		return err
	}

	enumValue := Alpha2Code(str)
	if len(enumValue) != 0 {
		if _, err := ByAlpha2CodeErr(enumValue); err != nil {
			return err
		}
	}

	*code = enumValue
	return nil
}

func (code Alpha2Code) Value() (value driver.Value, err error) {
	if code == "" {
		return "", nil
	}

	var language Language
	if language, err = ByAlpha2CodeErr(code); err != nil {
		return nil, err
	}

	return language.Alpha2Code().String(), nil
}

func (code Alpha2Code) Validate() (err error) {
	_, err = ByAlpha2CodeErr(code)
	return
}

func (code Alpha2Code) IsSet() bool {
	return len(string(code)) > 0
}

func (code Alpha2Code) String() string {
	return strings.ToLower(string(code))
}

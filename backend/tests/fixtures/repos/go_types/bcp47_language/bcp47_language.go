package bcp47_language

import (
	"database/sql/driver"
	"encoding/json"
	"fmt"

	"github.com/mikekonan/go-types/v2/language"
	stdLanguage "golang.org/x/text/language"
)

type Language struct {
	raw    string
	rawTag stdLanguage.Tag
}

func ByStrErr(code string) (result Language, err error) {
	if code == "" {
		return Language{}, fmt.Errorf("code is empty string")
	}

	result.rawTag, err = stdLanguage.Parse(code)
	if err != nil {
		return Language{}, fmt.Errorf("'%s' is not valid BCP47 language code", code)
	}

	result.raw = code
	return result, nil
}

func (l Language) BaseISO639Language() (language.Language, error) {
	baseRaw, _ := l.rawTag.Base()
	baseISO639Language, err := language.ByAlpha2CodeStrErr(baseRaw.String())
	if err != nil {
		return language.Language{}, err
	}

	return baseISO639Language, nil
}

func (l Language) String() string {
	return l.raw
}

func (l Language) Raw() stdLanguage.Tag {
	return l.rawTag
}

func (l *Language) UnmarshalJSON(data []byte) (err error) {
	var str string
	if err = json.Unmarshal(data, &str); err != nil {
		return err
	}

	entity, err := ByStrErr(str)
	if err != nil {
		return err
	}

	*l = entity
	return nil
}

func (l Language) MarshalJSON() ([]byte, error) {
	return json.Marshal(l.String())
}

func (l *Language) Value() (value driver.Value, err error) {
	return l.raw, nil
}

func (l Language) Validate() (err error) {
	_, err = ByStrErr(l.raw)
	return
}

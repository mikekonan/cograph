package language

import (
	"fmt"
	"strings"
)

type Language struct {
	name   Name
	alpha2 Alpha2Code
	alpha3 Alpha3Code
}

func (language Language) Name() Name { return language.name }

func (language Language) Alpha2Code() Alpha2Code { return language.alpha2 }

func (language Language) Alpha3Code() Alpha3Code { return language.alpha3 }

func ByAlpha2Code(code Alpha2Code) (result Language, ok bool) {
	result, ok = LanguageByAlpha2[strings.ToLower(code.String())]
	return
}

func ByAlpha2CodeStr(code string) (Language, bool) {
	return ByAlpha2Code(Alpha2Code(code))
}

func ByAlpha2CodeErr(code Alpha2Code) (result Language, err error) {
	var ok bool
	result, ok = ByAlpha2Code(code)
	if !ok {
		err = fmt.Errorf("'%s' is not valid ISO 639-1 code", code)
	}

	return
}

func ByAlpha2CodeStrErr(code string) (Language, error) {
	return ByAlpha2CodeErr(Alpha2Code(code))
}

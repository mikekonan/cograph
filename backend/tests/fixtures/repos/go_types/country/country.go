package country

import "strings"

type Country struct {
	name   Name
	alpha2 Alpha2Code
	alpha3 Alpha3Code
}

func (country Country) Name() Name { return country.name }

func (country Country) Alpha2Code() Alpha2Code { return country.alpha2 }

func (country Country) Alpha3Code() Alpha3Code { return country.alpha3 }

func ByAlpha2Code(code Alpha2Code) (result Country, ok bool) {
	result, ok = CountryByAlpha2[strings.ToUpper(code.String())]
	return
}

func ByAlpha2CodeStr(code string) (Country, bool) {
	return ByAlpha2Code(Alpha2Code(code))
}

func ByAlpha2CodeErr(code Alpha2Code) (result Country, err error) {
	var ok bool
	result, ok = ByAlpha2Code(code)
	if !ok {
		err = newInvalidDataError(string(code), standardISO3166alpha2)
	}

	return
}

func ByAlpha2CodeStrErr(code string) (Country, error) {
	return ByAlpha2CodeErr(Alpha2Code(code))
}

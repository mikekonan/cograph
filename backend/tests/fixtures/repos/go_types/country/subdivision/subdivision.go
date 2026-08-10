package subdivision

import (
	"fmt"
	"strings"

	"github.com/mikekonan/go-types/v2/country"
)

type Subdivision struct {
	name        Name
	code        Code
	countryCode country.Alpha2Code
	category    Category
}

func (s Subdivision) Name() Name { return s.name }

func (s Subdivision) Code() Code { return s.code }

func (s Subdivision) CountryCode() country.Alpha2Code { return s.countryCode }

func (s Subdivision) Category() Category { return s.category }

func (s Subdivision) CodeStr() string { return s.code.String() }

func (s Subdivision) AlphaCode() string { return s.code.AlphaCode() }

func (s Subdivision) CountryCodeStr() string { return s.countryCode.String() }

func ByCode(code Code) (result Subdivision, ok bool) {
	result, ok = subdivisionByCode[strings.ToUpper(code.String())]
	return
}

func ByCodeStr(code string) (Subdivision, bool) {
	return ByCode(Code(code))
}

func ByCodeErr(code Code) (result Subdivision, err error) {
	var ok bool
	result, ok = ByCode(code)
	if !ok {
		err = fmt.Errorf("'%s' is not valid ISO-3166-2 subdivision code", code)
	}

	return
}

func ByCountryCode(code country.Alpha2Code) (result []Subdivision, ok bool) {
	src, ok := subdivisionsByCountry[strings.ToUpper(code.String())]
	if ok {
		result = make([]Subdivision, len(src))
		copy(result, src)
	}

	return
}

func ByCountryCodeErr(code country.Alpha2Code) (result []Subdivision, err error) {
	var ok bool
	result, ok = ByCountryCode(code)
	if !ok {
		err = fmt.Errorf("'%s' is not valid ISO-3166-1 alpha-2 country code", code)
	}

	return
}

func ValidateForCountry(countryCode country.Alpha2Code, code string) error {
	return Code(code).ValidateForCountry(countryCode)
}

func ValidateForCountryStr(countryCode string, code string) error {
	return Code(code).ValidateForCountry(country.Alpha2Code(countryCode))
}

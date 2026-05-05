package subdivision

import (
	"database/sql/driver"
	"fmt"
	"strings"

	"github.com/mikekonan/go-types/v2/country"
	"github.com/mikekonan/go-types/v2/internal/utils"
)

type Code string

func (code *Code) UnmarshalJSON(data []byte) error {
	str, isEmptyValue, err := utils.UnsafeStringFromJson(data)
	if err != nil {
		return err
	}

	if isEmptyValue {
		return nil
	}

	subdivision, err := ByCodeErr(Code(str))
	if err != nil {
		return err
	}

	*code = subdivision.Code()
	return nil
}

func (code Code) Value() (value driver.Value, err error) {
	if code == "" {
		return "", nil
	}

	var subdivision Subdivision
	if subdivision, err = ByCodeErr(code); err != nil {
		return nil, err
	}

	return subdivision.Code().String(), nil
}

func (code Code) Validate() (err error) {
	_, err = ByCodeErr(code)
	return
}

func (code Code) ValidateForCountry(countryCode country.Alpha2Code) error {
	str := strings.ToUpper(code.String())
	idx := strings.IndexByte(str, '-')
	if idx != 2 {
		return fmt.Errorf("'%s' is not valid ISO-3166-2 subdivision code format", code)
	}

	prefix := country.Alpha2Code(str[:2])
	if _, err := country.ByAlpha2CodeErr(prefix); err != nil {
		return fmt.Errorf("'%s' contains unknown country prefix '%s'", code, prefix)
	}

	if !strings.EqualFold(prefix.String(), countryCode.String()) {
		return fmt.Errorf("subdivision '%s' belongs to country '%s', not '%s'", code, prefix, countryCode)
	}

	if _, err := ByCodeErr(code); err != nil {
		return err
	}

	return nil
}

func (code Code) ValidateForCountryStr(countryCode string) error {
	return code.ValidateForCountry(country.Alpha2Code(countryCode))
}

func (code Code) AlphaCode() string {
	s := string(code)
	idx := strings.IndexByte(s, '-')
	if idx < 0 {
		return s
	}
	return s[idx+1:]
}

func (code Code) IsSet() bool {
	return len(string(code)) > 0
}

func (code Code) String() string {
	return string(code)
}

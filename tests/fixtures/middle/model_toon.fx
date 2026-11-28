#ifndef NINESYNC_TOON_CONTROLLER
#define NINESYNC_TOON_CONTROLLER "MME Control"
#define NINESYNC_TOON_INTENSITY 0.8
#define NINESYNC_TOON_THRESHOLD 0.5
#endif

float4 ModelDiffuse : DIFFUSE < string Object = "Geometry"; >;
float3 LightRay : DIRECTION < string Object = "Light"; >;
float ControllerX : CONTROLOBJECT < string name = NINESYNC_TOON_CONTROLLER; string item = "X"; >;
float Intensity < string UIName = "Intensity"; > = NINESYNC_TOON_INTENSITY;
float Threshold < string UIName = "Toon threshold"; > = NINESYNC_TOON_THRESHOLD;
float4x4 WVP : WORLDVIEWPROJECTION;

bool use_texture;
texture BaseMap : MATERIALTEXTURE;
sampler2D Base = sampler_state {
    Texture = <BaseMap>;
    MinFilter = POINT;
    MagFilter = POINT;
    AddressU = CLAMP;
    AddressV = CLAMP;
};

struct Surface {
    float4 position : POSITION0;
    float3 normal : NORMAL0;
    float2 uv : TEXCOORD0;
};

Surface VS(Surface v) {
    Surface o;
    o.position = mul(v.position, WVP);
    o.normal = v.normal;
    o.uv = v.uv;
    return o;
}

float4 PS(Surface v) : COLOR0 {
    float band = dot(normalize(v.normal), -LightRay) >= Threshold ? 1.0 : 0.25;
    float4 base = ModelDiffuse;
    if (use_texture) {
        base *= tex2D(Base, v.uv);
    }
    return float4(base.rgb * (band * Intensity * ControllerX), base.a);
}

technique Model <
    string MMDPass = "object";
#ifndef NINESYNC_PMX_TOON
    string Subset = "1";
#endif
> {
    pass SurfacePass {
        VertexShader = compile vs_3_0 VS();
        PixelShader = compile ps_3_0 PS();
    }
}

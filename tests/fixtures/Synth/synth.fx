float3 Tint < string UIName = "Tint"; string UIWidget = "Color"; > = float3(1, 0.5, 0.25);
float Spread < string UIWidget = "Slider"; float UIMin = 0; float UIMax = 10; > = 2.5;
bool Hidden < bool UIVisible = false; > = true;
int Copies = 4;
int index;
float Time : TIME;
float Elapsed : ELAPSEDTIME;
float2 Viewport : VIEWPORTPIXELSIZE;
float3 LightDir : DIRECTION < string Object = "Light"; >;
float3 LightPos : POSITION < string Object = "Light"; >;
float3 CamPos : POSITION < string Object = "Camera"; >;
float3 CamDir : DIRECTION < string Object = "Camera"; >;
float4x4 WVP : WORLDVIEWPROJECTION;
float4x4 LWVP : WORLDVIEWPROJECTION < string Object = "Light"; >;
float4x4 VI : VIEWINVERSE;
float4x4 WIT : WORLDINVERSETRANSPOSE;
float4x4 WV : WORLDVIEW;
float4x4 SelfM : CONTROLOBJECT < string name = "(self)"; >;
float3 SelfXYZ : CONTROLOBJECT < string name = "(self)"; string item = "XYZ"; >;
float SelfRx : CONTROLOBJECT < string name = "(self)"; string item = "Rx"; >;
float Tr : CONTROLOBJECT < string name = "(self)"; string item = "Tr"; >;
float Si : CONTROLOBJECT < string name = "(self)"; string item = "Si"; >;
float3 LightColor : SPECULAR < string Object = "Light"; >;
float4x4 BoneM : CONTROLOBJECT < string name = "Model.pmx"; string item = "Bone"; >;
float3 BonePos : CONTROLOBJECT < string name = "Model.pmx"; string item = "Bone"; >;
float3 Other : CONTROLOBJECT < string name = "other.x"; >;
float MorphW : CONTROLOBJECT < string name = "Model.pmx"; string item = "Smile"; >;
static float weights[4] = {0.1, 0.2, 0.3, 0.4};

struct VS_OUT { float4 Pos : POSITION; float4 Col : TEXCOORD0; float2 Tex : TEXCOORD1; };

VS_OUT VS(float4 Pos : POSITION, float2 Tex : TEXCOORD0, float3 N : NORMAL)
{
    VS_OUT o = (VS_OUT)0;
    float3 p = Pos.xyz;
    int k = index;
    float acc = 0;
    for (int i = 0; i < 4; i++) { acc += weights[i] * (i == (k & 3) ? 2 : 1); }
    p.x += k * Spread + sin(Time * 3 + k) * 0.5;
    p.y += (k % 3) * 0.7 + (k / 3) * 0.2 + (k >> 1) + (k << 1) * 0.01 + ((k ^ 5) | 2) * 0.01;
    p += LightDir * 0.1 + SelfXYZ * 0.01 + mul(float4(1, 0, 0, 0), SelfM).xyz * 0.01 + SelfRx;
    p.z += weights[k & 3] + acc;
    if (!(k < 2) && Tr > 0.3 || k == 0) { p.y += 0.25; }
    float4 wp = mul(float4(p, 1), WVP);
    float4 lp = mul(float4(p, 1), LWVP);
    o.Pos = (k == 3) ? float4(0, 0, -2, 1) : wp;
    o.Col = float4(Tint * Tr, saturate(lp.z / lp.w)) + float4(Viewport / 4000, Elapsed, Si * 0.01);
    o.Col.xyz += mul(VI[3], WIT).xyz * 0.001 + CamPos * 0.0001 + CamDir * 0.01 + LightPos * 1e-5 + LightColor * 0.1 + mul(float4(p, 1), WV).xyz * 0.001;
    o.Tex = Tex;
    return o;
}

VS_OUT VS2(float4 Pos : POSITION, float2 Tex : TEXCOORD0)
{
    VS_OUT o = (VS_OUT)0;
    float acc = 0;
    for (int j = 0; j < (int)(Tex.x * 4 + 1); j++) { acc += 0.1; }
    o.Pos = mul(float4(Pos.xyz + float3(0, acc, 3), 1), WVP);
    o.Col = float4(acc, Tr, 0, 1);
    o.Tex = Tex;
    return o;
}

float4 PS(VS_OUT IN) : COLOR0 { return IN.Col; }
float4 PS2(VS_OUT IN) : COLOR0 { int a = (int)(IN.Tex.x * 8); return float4((a & 3) / 3.0, IN.Col.y, 0, 1); }

technique T < string MMDPass = "object"; string Script = "LoopByCount=Copies; LoopGetIndex=index; Pass=P0; LoopEnd=; Pass=P1;"; >
{
    pass P0 { VertexShader = compile vs_3_0 VS(); PixelShader = compile ps_3_0 PS(); }
    pass P1 { VertexShader = compile vs_3_0 VS2(); PixelShader = compile ps_3_0 PS2(); }
}
